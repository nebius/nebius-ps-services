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

<!-- FEATURE: FEAT-002 reqs=REQ-002,REQ-003 status=ready priority=P0 version=7 -->
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
`project_agent_instructions_lib/discovery.py` now separates the exact selected
target from the nearest-marker instruction discovery root. Focused disposable
Git tests cover enclosing-root and closer custom markers, no marker inside the
worktree, empty-marker behavior, global-policy isolation, human and override
targets, current-turn compatibility rendering, and terminal reload outcomes.
The owner reconciliation cases cover explicit and ambiguous paraphrases of
existing-user compatibility intent.
Retained lifecycle evidence also shows that rendering an inapplicable
`existing-sufficient` decision for a managed target could publish empty private
rules, while the corrected `needed` decision then failed with `UNSAFE_TARGET`
because the renderer accepted only byte-identical reuse of that owned path.

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

An initial Stop from `implementation-open` performs an owner-controlled,
compare-and-swap transition to `reconciliation-required` before returning that
prompt. The transition is epoch-neutral and clears the same stale receipt,
spec, rules, and planned-epoch bindings cleared by a genuine new prompt. A
Stop-generated continuation can therefore edit the canonical spec pair even
when the runtime does not emit `UserPromptSubmit`. Recursive Stop evaluation
remains fail-closed and does not repeat or bypass reconciliation.

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

Preflight the selected disposition against the fresh inspected target before
creating or updating private rendered rules. In particular,
`existing-sufficient` is renderable only when the active instruction is the
human-owned target or active human-owned override/fallback accepted by the
apply contract; a managed target fails before render publication.

Treat a current-session render revision as a compare-and-swap transition over
private evidence. Hold one nonblocking OS lock across fresh-manifest replay,
decision preflight, predecessor validation, rules replacement, and state
publication; an overlapping renderer fails without writing. Byte-identical
output remains idempotent. Changed output may atomically replace the canonical
mode-`0600` rules file only when the canonical mode-`0600` render state has the
exact schema, project and path ownership bindings, and digest for the bytes
still at that path. Recheck the predecessor file identity and bytes before
replacement, sync the replacement and directory, then publish the matching new
render state. If state publication fails after replacement, the old state and
new exact decision-rendered rules form a fail-closed recovery boundary: a
rerun under the same lock recognizes the already-published exact rules and
finishes state publication without repeating a repository effect.

Historical spec and decision digests describe the generation that owns the
predecessor rules; they need not equal a later reconciled generation. Require
their canonical shape and exact project/path/rules ownership bindings, while
freshly replaying the current manifest and decision before replacement and
letting lifecycle planning independently validate the new full spec digests.
Missing, malformed, ownership-mismatched, symlinked, hard-linked,
permission-unsafe, or concurrently changing predecessor evidence fails closed.
This transition grants no repository mutation or coordinator authority.

Treat plain-language user-dependence intent semantically rather than through a
keyword trigger. A statement that real users rely on the project and future
code or interface changes must remain safe is explicit compatibility intent,
even without `GA` or `backward compatibility`. Reconcile it into requirements
and design before implementation, then render the resulting project rules into
the current lifecycle plan so they govern the current change. When no active
same-directory instruction already supplies an equivalent contract, the
decision is `needed` and terminal apply persists these default rules:

- This project has existing users. Preserve supported behavior and public
  interfaces across changes; treat unintended compatibility breakage as a
  regression.
- Breaking a supported API, CLI contract, configuration or persisted format,
  or upgrade path requires explicit approval, a deprecation or migration plan,
  and regression coverage. Keep internals on one canonical path.

The protected surface includes public APIs and import paths, CLI commands,
flags, output and exit behavior, configuration schemas and defaults, persisted
formats, and upgrade paths. Private implementation details may be refactored
when those observable contracts remain intact. Global instructions remain
conflict context only: a personal no-compatibility default is neither copied
into the repository nor allowed to suppress explicit project intent. A
same-directory `AGENTS.override.md` stays authoritative and blocks automatic
generation if it omits a necessary rule.

Resolve project targeting independently from Codex instruction discovery. The
selected directory remains the receipt, evidence, target, and generated-file
scope. For a nonempty effective marker list, walk upward from that directory
through the enclosing Git root and use the nearest directory containing any
configured marker as the instruction-chain root. Search no directory above the
Git root and fail closed when no marker matches. When the marker list is empty,
use the selected directory directly and do not traverse parents. The manifest
continues to bind the effective marker configuration and instruction files;
replay recomputes the discovery root without adding a schema field, public
flag, or bypass path.

#### Selected Option

Resolve trusted installed coordinators from the canonical user-skill root,
retain a distinct source-test binding, keep PostToolUse as a silent evidence
recorder, consolidate semantic reconciliation at Stop, and describe the final
project-instructions outcome without implying unconditional file creation.
Make the initial Stop transition its own implementation-open lifecycle state
before requesting reconciliation instead of depending on another hook event.
Bind terminal decision evidence to the canonical current-session private
bundle, and let late successful write accounting invalidate later planning or
sealing evidence. Keep the existing fail-closed command contract and phase
machine. Reject inapplicable decisions before rendering, and permit revised
private rules only through a compare-and-swap that revalidates the exact state
and rules predecessors at the final replacement boundary. Give only a proven
matching-state publication I/O failure a distinct exact-rerun result. Make
explicit user-dependence intent durable through a `needed`
project rule unless an active selected-project instruction is already
sufficient, and derive the instruction chain from the nearest effective marker
ancestor without changing the selected-project target.

#### Alternatives Considered

Keeping `${CODEX_HOME}/skills` as a second default contradicts the installer
and Codex discovery contract. Trusting any script inside the current repository
would permit unreviewed worktree changes to obtain coordinator authority.
Removing PostToolUse would lose post-success failure discrimination and stale
epoch protection. Per-edit semantic inference or a persistent change-content
journal would make hooks document authors, duplicate Git evidence, and expand
private-state migration and privacy risk. Requiring `GA` or another magic word
would miss equivalent user intent. Treating the selected project as the Codex
discovery root in every case would reject valid nested projects; changing the
user's global marker configuration or adding a bypass flag would alter runtime
semantics outside the selected project. Relying on `UserPromptSubmit` after a
Stop continuation preserves the observed deadlock, while admitting canonical
spec edits throughout `implementation-open` weakens the phase boundary. A
Task Implementer-only retry or bypass would leave the lifecycle defect in every
other long-running consumer. Requiring byte identity forever strands corrected
decisions, while deleting or blindly overwriting the old render would discard
ownership evidence and weaken the fail-closed boundary.

#### Implementation Boundaries

Owned changes are the lifecycle hook's coordinator-root resolution and output
contract, PostToolUse registration metadata, focused tests and semantic evals,
lifecycle reference documentation, project-instructions outcome wording and
missing-target regression coverage, compatibility-intent reconciliation and
evaluation guidance, nested discovery-root resolution, skill README and
runtime instructions, root installation guidance, and changelog. Canonical
owner schemas, lifecycle phases, receipts, decision dispositions, and public
coordinator commands remain unchanged. The public coordinator retains bounded
receipt persistence and an opaque turn-token input while preserving raw turn
IDs for direct owner tests. The project-instructions renderer owns disposition
preflight, dual-predecessor validation, atomic private rules replacement,
matching render-state publication, and the bounded publication-incomplete
result; hooks do not order or perform that repair.

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
- TDD-011: Semantically equivalent statements that existing users depend on
  stable behavior produce the same compatibility requirements without a magic
  keyword, while conversational or ambiguous statements remain uncommitted.
- TDD-012: A global no-compatibility default is not copied and does not
  suppress a necessary project rule; an equivalent active project instruction
  yields `existing-sufficient`, while a same-directory override missing the
  rule fails closed.
- TDD-013: A selected nested project without a local `.git` resolves the
  nearest matching ancestor through the enclosing Git root, scans only that
  instruction chain, and still targets its own `AGENTS.md`. No matching marker
  fails closed, and an empty marker list uses only the selected directory.
- TDD-014: Current-session render and lifecycle planning inject needed
  compatibility rules before implementation; terminal apply, verify, seal,
  reload reporting, and fresh inspection preserve the intended file effect.
- TDD-015: Initial Stop from `implementation-open` atomically enters
  `reconciliation-required`, leaves the write epoch unchanged, clears stale
  planning evidence, and admits the canonical spec patch without an intervening
  `UserPromptSubmit`; recursive Stop still terminates fail-closed.
- TDD-016: A managed target plus `existing-sufficient` fails during render
  before creating or updating rules or render state.
- TDD-017: A current-session revision from exactly owned empty rules to needed
  nonempty rules atomically updates the rules and their render-state digests,
  remains repository-read-only, and is admitted by lifecycle planning.
- TDD-018: Missing, malformed, mismatched, unsafe, hard-linked, or tampered
  predecessor evidence blocks a changed rerender without altering either
  private rules or repository state. A state replacement or content change
  after initial validation but before the final compare-and-swap is detected
  before rules replacement.
- TDD-019: An overlapping renderer loses the nonblocking bundle lock and
  writes nothing. An injected private I/O failure during matching-state
  publication returns `RENDER_STATE_PUBLICATION_INCOMPLETE`; one exact CLI
  rerun publishes matching state and re-syncs its parent directory when a
  prior replacement completed before directory sync failed. Unsafe ownership,
  mode, link, or schema failures remain `UNSAFE_TARGET`. Lock creation or
  metadata failures return one bounded structured blocker without exposing
  private paths or writing render artifacts.

#### Validation Plan

Run focused hook and owner coordinator tests, semantic reconciliation evals,
render revision and adversarial ownership tests, syntax and lint checks,
changed-scope review, security review, installer refresh tests, and alignment
before installation.

#### Test Plan

Cover every coordinator action and lifecycle phase, both coordinator owners,
canonical and noncanonical roots, path normalization, symlinks, missing files,
interpreter mismatch, selected-project mismatch, repeated PostToolUse output,
late successful material-write recording, unchanged-spec sealing,
missing-target `not-needed`, semantic reconciliation categories,
compatibility-intent paraphrases, global-policy isolation, missing and
human-owned targets, same-directory overrides, nearest-marker nested discovery,
empty marker configuration, current-session injection, terminal apply/verify,
reload reporting, and the Stop-to-reconciliation transition without a prompt
event. Cover managed-target disposition preflight, owned empty-to-nonempty
rerender, idempotent identical output, stale state, digest mismatch, unsafe
metadata, hard links, concurrent lock contention, interrupted state
publication recovery, concurrent predecessor change, and lifecycle-plan
admission of the revised bundle.

#### Evaluation Plan

Reproduce the original `planning-required` rejection using the faulty root,
then confirm the repaired installed path reaches owner `inspect` without
admitting an arbitrary material command. In a separately authorized fresh
runtime, make multiple safe edits and verify no routine reconciliation context
or status text appears before exactly one terminal request. Confirm that the
request calls for an explicit project-instructions outcome and says
`not-needed` leaves a missing `AGENTS.md` absent. Treat any generic
host-rendered hook-completion row as a runtime observation, not source proof.
Also confirm that user-dependence intent yields the compatibility rules without
requiring prescribed words, that the rules are present during the current
implementation plan, and that terminal persistence is reported separately from
fresh-session instruction discovery. Reproduce the retained empty-render
deadlock in a disposable bundle and confirm the corrected source rejects the
invalid first decision or atomically admits an exactly owned revised render;
do not alter the retained failed-session evidence.

#### Rollout And Rollback

Install the reviewed hook with a recoverable backup, restart Codex, review and
trust the changed hook, then use a fresh selected-project session. Restore the
exact backup if the fresh probe fails before accepting further project writes.

#### Done Definition

The lifecycle reaches a receipt-bound terminal state using canonical installed
coordinators, repeated writes stay silently accounted, semantic reconciliation
occurs once at Stop, unchanged specs can seal after current-epoch validation,
invalid dispositions cannot publish render evidence, exactly owned decision
revisions cannot deadlock on their own private rules, unowned changes remain
blocked,
explicit compatibility intent governs the current change and future sessions,
nested selected projects follow effective Codex root discovery without changing
their target scope, and noncanonical commands still fail closed.

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

<!-- FEATURE: FEAT-007 reqs=REQ-008 status=ready priority=P0 version=6 -->
### FEAT-007: Single-report troubleshoot safety obligation

#### Requirements Covered

- REQ-008: Require a concise, accurate outcome report without making ordinary report quality a workflow blocker or requesting an automatic duplicate after sensitive output.

#### Context Evidence

The advisory v4 contract fixed ordinary report-format loops, but its sensitive
report branch still returned `decision: block` and asked the model to rewrite
the whole response. The host had already recorded the first assistant message,
so the feedback created a second assistant response. A non-sensitive but still
malformed rewrite then fell through to `advisory_incomplete`, leaving two
partially similar reports and no transactionally delivered valid report.
Source and installed payloads matched and only one shared Stop arbiter was
registered, localizing the failure to the source contract rather than install
drift or duplicate registration.

The subsequent strict local-reference rule also treated proven-contained
Markdown destinations, absolute or home-relative paths, local `file:` URIs,
and unrelated prose such as dotted names or slash commands as the same fatal
safety class as secrets and containment escape. The selected correction uses
the resolved Git repository root as the single authorization boundary and
separates harmless format defects from unsafe destinations.

#### Design Details

At `UserPromptSubmit`, every explicit `$troubleshoot` invocation keeps the
session-private, turn-bound `troubleshoot-report-obligation.json` sidecar. The
sidecar is an invocation marker and transactional delivery record; it is not a
general workflow lock. Schema v3 contains exactly `schema`, `workspace_hash`,
`session_hash`, `turn_hash`, and `status`. Its statuses are `active`,
`delivered`, `advisory_incomplete`, `sensitive_detected`, and `fallback`.
There is no correction counter or report-correction tool gate. Normal output
has one canonical compact shape:

1. `Outcome`: classification, confidence, fixed scope, and current state.
2. `Root Cause And Fix`: root cause and changes made.
3. `Verification`: verified scope and explicitly unverified scope.
4. `Next Action`: owner, action, and done condition.

Use field-specific validation. Confidence is exactly `High`, `Medium`, `Low`,
or `Unknown`; short enum values are valid. Narrative fields reject empty,
placeholder, or sensitive content but do not use a single arbitrary length
threshold. `DIAGNOSED-FIXED` is admitted only when the causal owner and earliest
divergence are proven, an owner-correct repair is applied, and the reproducer,
focused regression, and source or boundary checks pass for the named scope.
Installation, deployment, restart, or live replay may remain under `Not
verified` with an exact next action. `VERIFIED_FIXED` still requires complete
end-to-end proof and no material unverified item in the declared scope.
`DIAGNOSED_NOT_FIXED` remains the diagnosed-but-unrepaired outcome. Do not add
an underscore alias or a second parser path.

The compact authoring notice must fit inside the existing 800-character hook
context limit together with every supported profile and pending-message
combination. It states the four sections, every required field, the current six
classifications, the no-secret rule, and the local-reference grammar before the
model responds.
Prefer local file labels as plain inline-code repository-relative paths using
`/`, optionally followed by a positive `:line`. Use one typed local-target
classifier for inline and Markdown references. It accepts a
repository-relative, platform-native absolute, home-relative, or local `file:`
target only when decoding and canonical resolution keep the destination inside
the Git repository root derived from the event working directory. Existing
candidates resolve symlinks; nonexistent candidates use lexical containment
beneath the deepest existing ancestor. Without a proven Git root, absolute,
home-relative, and local `file:` forms remain indeterminate and fatal. Parse
balanced or escaped Markdown destinations and optional titles before
classification; ambiguous link syntax and renderer-active schemes remain
fatal. A local `file:` URI requires an empty authority and forbids credentials,
queries, fragments, control bytes, ambiguous or residual encoded traversal and
separators, and non-native path syntax. Parent or symlink escape, outside-root
targets, UNC or remote targets, and ambiguous destinations remain fatal.

Classify references internally as safe repository-local, format-only, or
unsafe private/escape without changing sidecar schema v3. Safe references can
be delivered. A contained wrapper or authoring defect becomes the existing
`advisory_incomplete` outcome. Only the unsafe class and independent sensitive
content become `sensitive_detected`. Ordinary dotted words, slash commands,
and route-like prose are not local references. Exempt only the exact span of a
parsed, contained target from generic home-path detection; continue scanning
its raw and decoded target, label, surrounding prose, and all other fields for
secrets, private hosts, private IPs, and credentials. Repository containment
never certifies referenced file content as safe.

Apply the same resolver to strict exhausted-report validation, but normalize a
contained absolute, home-relative, or URI value to repository-relative form in
generated fallback output and redact anything unsafe or indeterminate. Replace
an over-limit Markdown, inline-code, or URL value atomically rather than
truncate through its syntax. Public HTTPS targets remain subject to
private-host checks. Public HTTP or another nonprivate wrapper defect is
advisory rather than sensitive.

For an ordinary non-exhausted Stop, valid concise output becomes delivered.
Malformed, incomplete, unsupported, `FAIL`, or `UNKNOWN` content records
`advisory_incomplete` and returns `continue: true`; it requests no correction,
denies no later tool, and emits no generated fallback report. The skill, not
the hook, continues safe causal work while another bounded experiment could
change a decision. A bounded optional `Evidence Appendix` may summarize the
internal architecture, component, timeline, log, hypothesis, code, and
completion ledgers when requested or decision-relevant, but it is never a
normal completeness prerequisite.

Sensitive or unsafe ordinary output atomically closes as `sensitive_detected`
and returns `continue: false` with one compact, non-report system warning that
echoes none of the response. It never returns `decision: block` and never asks
the model for a replacement report. Repeated Stop evaluation emits no second
warning. A close-write failure returns a generic terminal state warning without
reflecting the report. This is post-generation terminal detection, not
sanitization or suppression: the host may already have rendered the assistant
message, so prevention depends on the upfront authoring contract.

Hard boundaries remain separate. Exact remediation-budget exhaustion keeps its
strict marker-bound concise report, one bounded correction, terminal fallback,
and `DIAGNOSED-FIXED` prohibition. `fallback` is not reused for ordinary
sensitive detection. Invalid trusted coordination state, missing authority,
and peer Stop policy stay fail-closed. A v2 sidecar is never rewritten or
migrated: resumed use fails closed with a fresh-session instruction, and
activation preflight aborts while any active v2 obligation exists.

The shared Stop arbiter evaluates every delegate even after one returns a
terminal result so project-spec, SDLC, and prompt-session transitions still
run. It retains the first terminal result in delegate order, gives terminal
results precedence over blockers, never replaces an earlier terminal with a
later result or failure, and finalizes a ready troubleshoot report exactly
once after delegate evaluation. Every byte-identical owner copy carries this
same composition logic.
If valid-report finalization fails after a peer already returned terminal, keep
that first terminal result and append only a generic trusted-state warning; do
not expose the finalizer result or silently claim delivery. Only the guard's
explicit internal finalization acknowledgement counts as success; a pass-shaped
result without that acknowledgement fails closed even when no peer is terminal.

#### Selected Option

Retain the session-private sidecar and shared Stop delegate, but make report
authoring prevention explicit and make sensitive-output handling deterministic
and terminal. Troubleshooting reasoning remains agentic; report validation,
state transitions, and Stop composition remain deterministic workflows.

#### Alternatives Considered

Keeping the model-authored correction reproduces duplicate user-visible
responses. Falling back only after a malformed correction fixes final state but
does not remove the duplicate. Removing the hook loses transactional peer
composition and strict exhaustion handling. A host-level pre-display filter
could guarantee suppression, but the current Stop interface cannot; revisit
that option only if the host adds a documented pre-render interception point.
Supporting both v2 and v3 state would create two canonical paths and contradict
the fail-fast migration policy. A persistent local/private mode or additional
allowed-root list would create mode confusion and is unnecessary when the
resolved Git root is the single containment boundary.

#### Implementation Boundaries

Owned surfaces are troubleshoot skill/report contracts, the remediation guard,
shared arbiter copies, hook documentation, process evals, and installer and
byte-parity validation. Hook registrations and status messages remain
unchanged. Hosted-process termination and pre-render suppression remain outside
local hook control.

#### Test-First Success Criteria

- TDD-001: Exact `DIAGNOSED-FIXED` output accepts concise confidence and a
  source-fixed/runtime-pending scope, the current classification vocabulary,
  and safe repository-contained relative, absolute, home-relative, and local
  URI references without requiring legacy sections.
- TDD-002: Ordinary incomplete, malformed, tool-error, partial, `FAIL`, and
  `UNKNOWN` reports return `continue: true`, record `advisory_incomplete`, emit
  no fallback, and do not deny later tools.
- TDD-003: Sensitive or unsafe ordinary output closes once as
  `sensitive_detected`, returns one terminal non-report warning, never requests
  continuation, never denies a later tool, and remains idempotent across a
  repeated Stop or state-write failure.
- TDD-004: Exact attempt/time exhaustion remains terminal, disallows
  `DIAGNOSED-FIXED`, and preserves the configured 5/120 defaults and 10/180
  maxima.
- TDD-005: All Stop delegates run for state effects, first-terminal precedence
  is deterministic, a valid report finalizes exactly once, and an advisory
  report never competes with peer arbitration. A finalization failure preserves
  the first peer terminal while surfacing one generic state warning.
- TDD-006: v2 resume fails with a fresh-session instruction, all prompt-context
  paths include every required field and fit 800 characters, and the
  safe-reference matrix covers nested working directories, sibling projects,
  ordinary and exhausted-fallback containment, local Markdown links,
  absolute/home/file URI targets, encoded and symlink escape, public URLs,
  nonreference prose, and API routes.

#### Validation Plan

Run focused hook, arbiter-composition, skill-contract, process-eval, installer
registration, syntax, lint, security, changed-scope review, alignment, and
adversarial reference and sensitive-content checks.

#### Test Plan

Use disposable task-state roots and synthetic turn IDs for every classification,
valid short confidence, source-fixed/runtime-pending proof, missing fields,
ordinary advisory behavior, sensitive output, safe and unsafe references, v2
resume, state-write failure, context bounds, exhaustion details, inclusive
limits, interruption, and other Stop delegates.

#### Evaluation Plan

Recreate a no-marker ordinary turn and confirm Stop never requests continuation
for report quality alone. Recreate the installed-runtime-pending case and
confirm the source repair is reported as `DIAGNOSED-FIXED` with a concise exact
activation action. In a disposable fresh session, force fake sensitive content
and verify the original may remain visible but no synthetic continuation or
second assistant report is created and peer lifecycle cleanup still occurs.

#### Rollout And Rollback

Land and validate source first. Before activation, run the bounded read-only
preflight script, which prints schema/status counts only. It preserves
recognized terminal v1/v2 history and aborts for active legacy, unknown schemas,
unsupported statuses, unreadable, or malformed sidecars. Preserve the root-source
identity of an existing installed skill, create an exact private skill backup,
then perform a targeted sync that leaves its ownership marker unchanged.
Install the hook payload through the hook installer, retain its generated
backup, verify source/installed parity and one registered shared Stop arbiter,
restart Codex, review `/hooks`, and use a fresh disposable session. Rollback
restores the exact paired skill and hook backups, restarts Codex, and also
requires a fresh session; it never rewrites private obligation state.

#### Done Definition

Successful source repair is reported accurately and concisely, repository-
contained local references remain useful without weakening secret or escape
protection, ordinary report gaps never block safe troubleshooting, sensitive
output never causes an automatic replacement report, peer delegates retain
their state effects, strict budget boundaries remain enforced, and source
proof remains separate from installed and fresh-runtime activation.

<!-- /FEATURE: FEAT-007 -->

<!-- FEATURE: FEAT-008 reqs=REQ-009 status=ready priority=P0 version=3 -->
### FEAT-008: Objective-owned hybrid prompt-session intake

#### Requirements Covered

- REQ-009: Always deliver direct prompts while capturing eligible bound-workflow intent as a non-blocking sidecar.

#### Context Evidence

Task Implementer and `sdlc-start` already own private editable prompt files,
immutable execution revisions, explicit `run` routing, and workflow-specific
state. Codex `UserPromptSubmit` supplies session and turn identity plus the
direct prompt, while its command hook can add context or block but cannot
perform semantic rewriting. Runtime evidence showed that a stale prompt writer
claim could make this capture hook stop a fresh managed-lane session before the
agent received the prompt. The first non-blocking repair still staged every
otherwise eligible prompt body in a private `raw.md` before the agent could
classify skill, shell, delivery, status, conversation, or unrelated turns.
That body-first journal conflicts with data minimization and makes downstream
classification too late to prevent over-capture. The repository already
composes terminal workflow checks through one shared Stop arbiter.

#### Design Details

Keep the separately owned internal `prompt-session-intake` skill, but make its
`UserPromptSubmit` process an unconditional pass-through boundary. After an
exact explicit Task Implementer or SDLC binding, or an unambiguous compatible
active objective, it may acquire only a bounded session-state lock and
atomically stage an event-v2 metadata receipt with causal session/turn,
project, workflow, submitted digest, versioned operation identity, and an
unguessable acceptance token. It never writes the prompt body or `raw.md`.
Recognized secrets create no event. Binding, writer, ambiguity, unsafe-state,
or unexpected failures return bounded capture-skipped context or no context
with `continue: true`; they never stop the user turn. The hook never
semantically edits a prompt or invokes a workflow.

The current agent starts handling the direct prompt normally. Prompt capture is
a subordinate same-turn sidecar. The coordinator records one disposition:
`merge`, `noop`, or `sensitive`. Merge classifications remain intent, steering,
constraint, clarification answer, and acceptance change. No-op reasons cover
skill or workflow invocation, shell or tool control, delivery or operational
control, agent/session/response control, status or inquiry, conversation,
unrelated content, and already-recorded intent. A sensitive disposition
atomically removes submitted-digest, operation-token, and body-derived state.

For a merge disposition, the agent writes only a mode-0600
`project-intent.md`. It selects durable objectives, scope, behavior, API/CLI/
configuration/schema/data contracts, architecture decisions, constraints,
acceptance outcomes, domain facts and examples, priorities, non-goals,
trade-offs, corrections, clarification answers, and rollout or operational
requirements. Mixed turns omit ephemeral skill, shell, tool, delivery,
orchestration, status, response-style, and conversation clauses. A command is
retained only when it declaratively defines a project interface, example, or
verification contract. Losslessness applies to the selected project-intent
projection: preserve its facts, values, negations, decisions, uncertainty,
examples, references, constraints, and acceptance outcomes without retaining
the excluded wrapper.

Acceptance compares the objective prompt's recorded base digest, then delegates
only the project-intent merge to the owning workflow adapter. The adapter
rehashes the accepted projection and embeds one marker bound to both the
versioned operation ID and projection digest. Consumption verifies that exact
binding, preventing an accepted projection from being replaced before merge.
An exact operation retry returns the already-applied result. A byte-identical
projection already recorded by another operation is a terminal no-op; semantic
paraphrase detection remains agent-owned. Concurrent same-base operations keep
the conservative prompt CAS: one wins and a loser reports capture drift without
overwrite, automatic rebase, Stop continuation, or impact on direct work.

Keep one metadata-only event namespace for provenance and one canonical
objective prompt. Event-v2 uses a version-separated directory and operation
domain so event-v1 files and their raw journals remain inert, unread, and
unmodified without a compatibility reader or migration. A monotonic
current-event receipt and current Codex session digest authorize first
classification; later turns cannot classify an older staged event from new
context. An already-accepted immutable projection may finish its exact merge
and consume transition. Session identity binds provenance only; it is not an
execution or capture lease. The full prompt ID remains canonical objective
identity. Fresh sessions stage capture only when exactly one compatible active
objective exists; no candidate stays capture-inert and multiple candidates
skip capture without blocking the prompt. The registry retains bounded writer
session provenance for diagnostics but never rejects a current turn because a
different or stale writer value exists.

For Task Implementer, resolve logical project identity through the validated
workspace manifest. Treat `primary_root + scope` and the manifest-proven lane
`source_root` as aliases of one objective for attachment, writer-provenance
updates, objective replacement, and result validation. Continue to reject a
project that is not one of those two canonical identities.

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
prompt adapters. Direct accepted turns may update the canonical prompt but do
not call a start/resume path. Only the explicit public `run` action executes a
workflow, and manual prompt edits remain inert until that action. The existing
global-context hook stays lightweight. Prompt intake contributes a cleanup
delegate to the shared Stop arbiter rather than registering a competing Stop
hook; incomplete or invalid capture never makes that delegate block or request
continuation.

#### Selected Option

Make direct prompt delivery authoritative and prompt capture subordinate. Use
non-blocking metadata-only hooks, coordinator-owned semantic projection, thin
prompt-only workflow adapters, objective-owned canonical prompts, and
session-owned provenance. Bind every accepted projection to its operation,
retain prompt CAS, exact retry identity, secret non-persistence, and the hard
v3 prompt schema. Keep installation and fresh-runtime activation outside
source implementation.

#### Alternatives Considered

Letting the hook rewrite, keyword-filter, or run prompts would exceed the hook
contract and put semantic judgment in a concurrent command process. A raw-body
journal followed by downstream filtering is rejected because it persists
excluded input before the decision. A hook-side denylist is rejected because
it cannot distinguish immediate shell execution from a shell command that
defines project behavior, and it cannot project mixed turns safely. Blocking
prompt delivery to preserve capture ordering makes auxiliary state more
authoritative than the user journey. Automatic rebase after concurrent drift
is rejected because it cannot prove that intervening bytes were capture-owned
rather than a manual edit. One prompt file per session would fragment a
long-lived objective across sessions. Dual event-v1/event-v2 readers or writers
would create compatibility paths and prolong private-state ambiguity.

#### Implementation Boundaries

Owned surfaces are the new prompt-session-intake skill and hook payload, shared
Stop-arbiter composition, Task Implementer and SDLC prompt-workspace adapters
and schema migrations, focused tests, installer/catalog metadata, workflow and
root documentation, and changelog. Runtime installation, registration, trust,
restart, and fresh-session validation require separate explicit authorization.

#### Test-First Success Criteria

- TDD-001: The submit-hook process returns non-blocking output for bound,
  unbound, secret-bearing, stale-writer, conflicting-binding, unsafe-state, and
  unexpected-error inputs without exposing raw content or private tokens.
- TDD-002: An exact bound root-agent turn stages one secure event-v2 metadata
  receipt and no body journal. Recognized secret-bearing input reaches the
  agent but creates no event, projection, or canonical prompt update.
- TDD-003: Merge, no-op, and sensitive dispositions enforce their exclusive
  field sets. The semantic corpus covers every allowlisted project-intent form,
  every no-op reason, mixed turns, commands as data, contextual clarification,
  duplicate intent, and secret-plus-intent input for both workflows.
- TDD-004: Task Implementer and SDLC adapters merge only the accepted
  project-intent projection exactly once, bind its digest to the operation
  marker, reject projection substitution, and never enter a start/resume path.
  Explicit `run` and manual-edit behavior remain unchanged.
- TDD-005: One-time v2-to-v3 migration preserves objective and run evidence,
  prefixes filenames with collision-safe references, rejects ambiguous refs,
  and leaves no v2 write path.
- TDD-006: Current-session and current-event claims reject stale staged-event
  classification; event-v1 records remain inert while event-v2 stages in its
  separate namespace. Accepted immutable transitions recover only by exact
  projection and result identity.
- TDD-007: Primary and lane Task project identities attach to and update one
  logical registry objective, while unrelated projects remain capture-inert.
- TDD-008: Prompt intake composes through the single Stop arbiter, never blocks
  Stop for incomplete capture, and does not make the generic UserPromptSubmit
  hook route either workflow.

#### Validation Plan

Run focused prompt-intake and both prompt-workspace suites, hook composition
and installer contract tests, syntax and lint checks, adversarial security
review, changed-scope code review, skill-folder alignment, and project-wide
alignment. Report source proof separately from every runtime gate.

#### Test Plan

Use disposable homes, repositories, sessions, prompt workspaces, and synthetic
hook payloads for all wrapper pass-through, binding, primary/lane identity,
prompt migration, event-v1 isolation, current-event authorization,
dispositions, selective projections, projection substitution, exact and
semantic duplicates, reference, state transition, crash boundaries,
concurrency, secret, CAS, manual-edit, workflow-isolation, non-blocking Stop,
and recovery paths. Retain immutable revision comparisons across prompt
migration and explicit rerun cases.

#### Evaluation Plan

In a separately authorized fresh runtime, bind one session per workflow,
exercise managed-lane direct project intent, no-op categories, mixed prompts,
commands as data, secrets, stale event receipts, stale writer provenance,
ambiguous attachment, manual edit, and ref-based explicit rerun. Verify every
direct prompt reaches the agent, only the project-intent projection reaches the
canonical prompt once, and run counts change only after explicit `run`.

#### Rollout And Rollback

Land and validate source first. Later install through the repository installer
with recoverable hook backups, restart and review trust, then probe a fresh
session. Before any accepted v3 write, migration retains recoverable source
prompt bytes; rollback restores the exact prior runtime hook and uses the
preserved private state for explicit recovery rather than reviving v2 writers.

#### Done Definition

Both workflows share one non-blocking session-intake boundary, retain
independent explicit execution ownership, use canonical v3 prompt identity with
stable user-facing refs, preserve manual control and immutable evidence, and
pass the source validation matrix without claiming runtime activation. A Task
Implementer prompt submitted from either its primary project or verified
managed lane reaches the current agent and, when safe and material, is captured
in the same canonical objective prompt.

<!-- /FEATURE: FEAT-008 -->

<!-- FEATURE: FEAT-009 reqs=REQ-010 status=ready priority=P0 version=3 -->
### FEAT-009: Evidence-intensive troubleshooting and technology playbooks

#### Requirements Covered

- REQ-010: Discover and verify the deployed stack, layered evidence, relevant code paths, and technology-specific failure domains before completion.

#### Context Evidence

The baseline troubleshoot workflow owns hypotheses, bounded experiments,
earliest-divergence localization, causal proof, remediation budgets, and
live-product evidence gates. The component matrix, timeline, layered log
ledger, code-debugging gate, controlled escalation, and technology playbooks
are internal decision evidence. Version 2 incorrectly coupled their complete
rendering to the user-visible Stop report, causing report gaps to interrupt
repair even when the missing detail did not change the next safe action.

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

Bind every internal conclusion to an explicit included and excluded system
boundary, exercised control and data paths, and incident-window start and end
with a clock basis. The generic component matrix names DNS or service-name
resolution and restart history explicitly. The internal canonical log ledger
contains exactly one record for each component, application/job,
container/orchestrator, service-manager, OS/kernel, network/firewall, storage,
and GPU or hardware layer. Missing, duplicate, unknown, or out-of-order layer
names invalidate an internal coverage claim, and evidence-bearing cells remain
independently substantive. The ordinary user-visible report projects only the
root cause, fix, verification scope, material gaps, and next action. It does not
serialize the internal ledgers or cross-validate their formatting at Stop.

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
causal rigor in the skill workflow. Keep criterion-level internal verdicts and
project only decision-relevant proof into the concise outcome classification.
The Stop hook enforces hard boundaries and report delivery, not investigative
ledger completeness.

#### Alternatives Considered

Removing internal ledgers would allow aggregate health to hide component gaps.
A universal cloud or cluster command runner would overreach authorization and
encode stale vendor behavior. Requiring unconditional broad retrieval would
conflict with observability admission and minimization. Keeping the full
version-2 report as a compatibility path would retain two canonical outputs.

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
- TDD-002: Contract and process tests reject unsupported internal conclusions,
  missing or duplicate canonical log layers, placeholder component identities
  or evidence cells, and empty boundary or incident-window fields. Hook tests
  accept a concise ordinary report without those tables and keep incomplete
  ordinary output advisory.
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

In disposable or authorized read-only targets, require complete internal
architecture, component, timeline, log, hypothesis, remediation, and validation
evidence, then grade the accuracy and clarity of its concise projection. A
healthy final state without causal replay remains mitigation, not a verified
fix.

#### Rollout And Rollback

Land and validate source first. Installation, hook registration/trust, restart,
and fresh-session behavior require a separate authorized rollout with parity
checks and recoverable hook backups. Roll back only the exact installed payload
if runtime validation fails; preserve reviewed source evidence for repair.

#### Done Definition

Every troubleshooting run accounts internally for design, infrastructure,
connectivity, configuration, runtime health, logs, and relevant code paths with
evidence-backed verdicts. Every conclusion stays restricted to its declared
boundary, exercised paths, and observation window. The user sees a compact,
accurate projection of the fixed scope, material gaps, and next action rather
than the full evidence ledger by default.

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

For delegated Task Implementer commits, trust the hook runtime or one exact
executable named `python3` or `python3.N` that resolves to the same file by
basename through the hook process PATH. This admits a canonical worker Python
version different from the hook runtime while rejecting arbitrary same-name
paths and wrappers. `task-start` and base-state `task-recover` return a
transient structured commit context containing that resolved executable, the
canonical helper, outer lifecycle cwd, worker root, raw `CODEX_THREAD_ID`,
evidence paths, and exact prepare argv. Persist only the session fingerprint;
name it explicitly as evidence in worker-facing output so it cannot be
mistaken for the helper's raw `--session-id` argument.
The same transitions return a transient result-publication context. Its exact
private result parent is the required external working directory, separating
worker-result control-plane state from selected-project mutations.
Arm and rearm similarly translate internal `dispatched_at` state into one
explicit `start_lease` output field, matching the task-start command contract
and removing coordinator-to-worker naming ambiguity. Their transient start
context also carries the exact assignment path, scope cwd, worktree, and
task-start argv. Confirmed running-task resume carries the equivalent recovery
context and argv. Workers consume those paths verbatim rather than transcribing
visually similar monorepo worktree names.
The result context also carries an exact draft path and publisher argv. The
publisher validates identity and shape, computes `result_sha256` from the
complete final unsigned record, canonicalizes `changed_paths` as a sorted
unique set, and creates the immutable result atomically, eliminating checksum
and ordering drift between worker composition and coordinator reads. A prior
ordering-only rejection is recoverable only when the failed task plane remains
bound to the same immutable result digest and direct-child commit and no other
task in the wave is failed.

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
  later prose references, malformed helpers, non-PATH interpreter or helper
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
  same-ref owners serialize. Mixed canonical hook/worker Python versions pass;
  arbitrary same-name interpreters and session-fingerprint substitution fail
  before staging, while raw session IDs remain absent from persisted run state.
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

<!-- FEATURE: FEAT-011 reqs=REQ-012 status=ready priority=P0 version=1 -->
### FEAT-011: Run-owned whole-lane checkpoint and generation open

#### Requirements Covered

- REQ-012: Checkpoint a dirty persistent Task Implementer lane before a run.

#### Context Evidence

Task Implementer currently captures the persistent lane `HEAD`, invokes
Worktree's private generation-acquire action, and registers repository claims
afterward. Worktree rejects any dirty lane before acquisition, while resume
reconciliation correctly requires an already-active generation to remain
clean. Worktree's integration commit already demonstrates temporary-index
candidate review, repository-root staging, normal-hook commits, direct-child
proof, and durable recovery, but ordinary integration excludes Task lanes.
A separate checkpoint commit followed by the current acquire call would leave
an interruptible idle-lane head/state gap and would allow another lane to claim
checkpoint paths between review and publication.

#### Design Details

Keep the five public Task Implementer actions unchanged. During `run`, after
prompt refinement, managed spec and instruction validation, and deterministic
task/claim compilation, use a private two-phase Worktree transaction before
the first generation and any worker resources. Preparation copies the real
index to a private temporary index, applies whole-repository `git add -A`, runs
cached-diff checks, writes the candidate tree, enumerates the full changed-path
set, adds exact checkpoint claims, checks every live lane for overlap, and
persists a mode-0600 journal containing only bounded identities and digests.
The coordinator reviews the complete candidate and applicable instructions;
unsafe, incoherent, or unsupported content stops before execution.

Execution acquires locks in repository claim, per-lane Git transition, then
interop/shared-commit order. It revalidates branch, head, status, index,
candidate, claims, lane state, and absence of Git operations; stages the real
index with `git add -A`; reruns cached checks; and creates one normal-hook
direct-child commit with the fixed message
`chore(task-implementer): checkpoint managed lane`. A clean candidate follows
the same transition without creating a commit. Before releasing locks, publish
the resulting post-checkpoint head as `lane_head`, lease and interop
`initial_head`, coordinator baseline, first-wave base, and active generation
claims. Initial acquisition and claims therefore become one recoverable
generation-open boundary; later replanning may only extend the active claim
superset through the existing claims transition.

Store the Worktree-owned checkpoint journal separately from exact-schema lane
v1 state. Before open, archive a compact Task Implementer preparation receipt
that binds the journal token, candidate tree, canonical path digest, and planned
claims digest; after open, archive the post-checkpoint run receipt. Peer claim
scans, lane reuse, integration, removal, and new generation
preflight treat a live journal as authoritative provisional ownership. Recovery
may adopt only an exact staged candidate or exact clean direct child. A
hook-modified tree enters review-required state and rotates the token so stale
evidence cannot adopt it; unexpected paths must pass a fresh conflict and
safety review before adoption. Never roll back Git state
automatically. Run commit hooks in a controlled process group with a bounded
checkpoint-specific timeout, terminate and reap that group on expiry, and
block until repository quiescence is proven.

Only the resource-free fresh-generation path may prepare a checkpoint.
Existing interop state continues to inspect the active/released lease and
requires a clean lane. `workspace init`, public integration, removal, primary
source dirt, wave promotion, and retained recovery resources keep their
existing owners and blockers. The old private fresh acquire action is removed
instead of retained as a compatibility path; existing lane and interop records
remain readable because their schemas do not change.

#### Selected Option

Use a Task Implementer-orchestrated, Worktree-owned preparation plus atomic
checkpoint-and-generation-open transaction with provisional whole-repository
claims and exact crash recovery.

#### Alternatives Considered

Requiring the lane to be manually cleaned preserves the current blocker but
provides no supported owner action. A separate public checkpoint action adds
ceremony and lets users move managed history independently of `run`. Calling
the general `$commit` transaction cannot publish lane generation state or
provisional claims atomically. Committing only selected-project paths can split
one coherent cross-project change and contradict repository-root `git add -A`.
Checkpointing active-generation or source-checkout dirt would blur lifecycle
ownership and invalidate existing resume and integration evidence.

#### Implementation Boundaries

Task Implementer owns run timing, complete candidate review, private receipt,
post-checkpoint baseline propagation, user-visible status, and public contract.
Worktree owns candidate/journal validation, lock ordering, provisional claim
conflicts, Git mutation, recovery, lane state, lease acquisition, and the
private generation-open command. The general `commit` workflow remains
unchanged.

#### Test-First Success Criteria

- TDD-001: One dirty idle lane containing root, selected-project, and sibling
  changes produces one exact direct-child checkpoint and one active generation
  whose initial head and claims match that commit; the primary remains
  unchanged.
- TDD-002: A clean lane creates no commit and opens the generation at the
  unchanged head, while active-generation dirt remains `WORKTREE_CONFLICT`.
- TDD-003: Exact staged and post-commit crash windows recover without a second
  commit or generation; drift, conflicts, unsupported paths, and hook failure
  fail closed without automatic Git cleanup.
- TDD-004: Concurrent runs serialize preparation and opening, and another lane
  cannot acquire an overlapping checkpoint or planned claim during any crash
  window.
- TDD-005: Coordinator, interop, wave, finalize, pending-generation, public
  integration, and rearm behavior all use the post-checkpoint baseline.
- TDD-006: Contract tests retain exactly five public actions and prove that
  only explicit `run` owns checkpoint behavior.

#### Validation Plan

Run focused Worktree transaction and Task Implementer interoperability tests,
then the full Worktree and Task Implementer suites, stateful skill validation,
changed-scope security and code review, lint, and project-wide alignment.

#### Test Plan

Use disposable primary and linked worktrees with isolated private state to
cover the whole-lane change matrix, exact candidate and claim derivation,
clean no-op, normal and hook-modified commits, staged and post-commit crash
recovery, timeout process cleanup, concurrent claim conflicts, active-run dirt,
post-checkpoint wave baselines, final public integration, and unchanged public
action metadata.

#### Evaluation Plan

In a separately authorized installed-runtime probe, start from a dirty idle
persistent lane spanning multiple project folders, invoke one explicit `run`,
and independently inspect the checkpoint ancestry, complete committed tree,
generation receipt, claims, clean lane, and unchanged primary checkout. Repeat
the same invocation to prove no duplicate checkpoint.

#### Rollout And Rollback

Land source, tests, specs, docs, and changelog together. Install Task
Implementer and Worktree together, verify source/installed parity, and require
a fresh session before live evaluation. Roll back only the installed source
bundle; retain any incomplete journal and Git state for exact recovery rather
than deleting or rewriting it.

#### Done Definition

One explicit Task Implementer `run` can safely preserve a reviewed dirty
persistent lane across project folders as its exact generation baseline, while
the primary checkout and other public actions remain untouched and every
failure retains recoverable evidence without automatic cleanup.

<!-- /FEATURE: FEAT-011 -->

<!-- FEATURE: FEAT-012 reqs=REQ-013 status=ready priority=P1 version=1 -->
### FEAT-012: Existing-only workspace reopen through the live lane anchor

#### Requirements Covered

- REQ-013: Reopen an existing Task Implementer workspace without lane mutation.

#### Context Evidence

Workspace identity already derives from the canonical Git common directory,
primary checkout, exact source ref, and project scope, so source-project and
owning-lane paths resolve the same private manifest. Current `workspace init`
calls Worktree lane ensure before editor launch; ensure refreshes an idle lane
and therefore rejects lane dirt even when the user only needs to reopen the
editor. Worktree already exposes private `anchor-inspect`, and Task Implementer
already invokes that read-only path during execution interop.

#### Design Details

Add the fifth explicit public action, `workspace reuse [project-folder]`, with
the same current-directory and project-path resolution as initialization. The
private CLI computes the existing manifest path, returns
`WORKSPACE_NOT_FOUND` before creating any private directory, validates the
workspace-v2 manifest and generated VS Code document, then verifies its live
Worktree anchor before invoking the existing editor launcher.

After manifest validation, bind the invocation itself to exactly the recorded
primary checkout or owning lane and exact project scope. This prevents an
unrelated linked worktree from aliasing the workspace through copied branch
source-ref metadata.

Keep live lane ownership in Worktree. Harden `anchor-inspect` for Task lanes by
reusing the existing complete live-lane validation, including branch-config
source-ref and incarnation checks, and require the recorded source ref still to
exist. Permit only lifecycle states that retain a valid live worktree; reject
creating, creation-recovery, removing, removed, or inconsistent state. Do not
call idle refresh, lane ensure, lifecycle recovery, or another public Worktree
action. Task Implementer compares the anchor with the verified workspace lane
ID, incarnation, name, branch, worktree, scope, source ref, common directory,
and primary checkout.

Return `status: reused`, the existing workspace and lane paths, and observed
lane state without prompt bodies or readiness claims. Editor failures remain
non-fatal warnings after validation succeeds. No workspace, prompt, run, or
lane schema changes and no migration are required.

#### Selected Option

Compose the existing workspace resolver, workspace verifier, and Worktree
anchor inspector behind a distinct existing-only public reopen action.

#### Alternatives Considered

Making `workspace init` ignore dirty lanes would weaken its convergence and
refresh contract. A new Worktree lane-inspect action would duplicate the
existing authoritative anchor path. `workspace open` and `workspace --reuse`
would add aliases instead of the user-selected canonical action. Cross-branch
scanning would make source identity ambiguous.

#### Implementation Boundaries

Task Implementer owns public syntax, workspace lookup and validation, exact
workspace-to-anchor comparison, result rendering, and editor launch. Worktree
owns live managed-lane identity validation. Existing initialization, prompt,
run, checkpoint, integration, removal, and editor-launch semantics remain
unchanged.

#### Test-First Success Criteria

- TDD-001: Primary-project and owning-lane invocations resolve the same
  manifest and generated VS Code workspace.
- TDD-002: Repeated reuse of dirty idle and execution-active live lanes leaves
  Git, lane, workspace, prompt, run, checkpoint, and claim state unchanged.
- TDD-003: Missing state creates nothing; unsafe, removed, or mismatched state
  fails before editor launch with the stable owner error.
- TDD-004: `workspace init` continues to reject a dirty idle lane while
  `workspace reuse` succeeds against that exact lane.
- TDD-005: Contract, metadata, eval, and verifier tests expose exactly five
  public actions and reject aliases.

#### Validation Plan

Run focused prompt-workspace, Worktree anchor, and interoperability tests,
then the complete Task Implementer and Worktree suites, skill validators,
Python and Markdown lint, changed-scope security and code review, and final
alignment.

#### Test Plan

Use disposable primary and linked worktrees with isolated private state. Cover
source and lane cwd aliases, clean and dirty lanes, active and retained live
states, repeated reuse, missing and unsafe workspace files, removed and
mismatched anchors, editor failures, unchanged prompt/run history, and the
regression that initialization still rejects dirty idle lanes.

#### Evaluation Plan

After separately authorized installation, reopen one disposable workspace from
both its source project and managed lane, confirm the same VS Code workspace is
targeted, and independently compare Git plus private state before and after.

#### Rollout And Rollback

Land source, tests, specs, docs, metadata, and changelog together. Source
validation does not install or activate the skill; installed parity and a
fresh-session editor probe remain separately authorized gates. Rolling back
the source removes only the action and leaves every existing workspace and lane
unchanged because no persisted schema was introduced.

#### Done Definition

One explicit source-project command reopens the exact existing managed
workspace without requiring private paths or changing Git or workflow state.

<!-- /FEATURE: FEAT-012 -->

<!-- FEATURE: FEAT-013 reqs=REQ-014 status=ready priority=P0 version=4 -->
### FEAT-013: Attested Task Implementer lifecycle bridge

#### Requirements Covered

- REQ-014: Bridge Task Implementer project contracts through the owner lifecycle.

#### Context Evidence

The project lifecycle currently binds every project-instructions command to the
current hook session's private bundle and selected checkout. Task Implementer
correctly retains its authoritative receipt and rendered state under the run
orchestration root and targets the active wave's integration checkout. The two
individually safe constraints are mutually exclusive when an explicit `run`
reaches worker dispatch. In the same path, `wave-plan` can checkpoint the
persistent lane inside a trusted helper subprocess, so generic effect analysis
does not observe the repository write and leaves the outer lifecycle in
`implementation-open`.

#### Design Details

Keep both authorities and add one narrow adapter validation path. A hidden,
read-only Task Implementer helper action accepts the original canonical
project-instructions command on standard input. It resolves the workspace and
run from the run-owned private root, validates current workspace/run/coordinator
state, identifies the active wave and live integration worktree, and compares
the command's project, canonical installed or sibling-source helper, inspect or
render action, Codex home, and every action-specific receipt, runtime,
manifest, decision, rules, and render-state path with the exact derived
contract. It returns only a bounded authorization result; it writes no private,
Git, lifecycle, or repository state. Apply and verify are not adapter actions;
they remain owned by the ordinary terminal lifecycle transition.

Git identity remains exact at the recorded branch. First preparation requires
the original wave base head. A retained correction requires its exact recorded
`contract_commit`; when sealed contract adoption advanced that head beyond the
prior integrated commit, the Task owner's contract-delta journal must still
attest the exact adoption commit, including its parent, fixed message, changed
paths, and committed blob digests.
Replan, prepare, lifecycle authorization, and dispatch all rebind the checkout
to the recorded linked-worktree registration and common Git directory; an
independent repository at the same path, branch, and commit fails closed.
The checkout may be clean or contain only
staged selected-project `docs/requirements.md` and `docs/design.md`, matching
Task Implementer's contract-compilation sequence.
Any unstaged, untracked, deleted, symlinked, sibling, renamed-from-another-path,
or other staged delta fails closed before attestation.

The lifecycle hook invokes that validator only when a canonical
project-instructions command fails the ordinary current-session bundle match.
It accepts the command only when the adapter result binds the current selected
lane project, exact original command digest, an implementation-open or
reconciliation-required phase, and an inspect or render action. Ordinary lifecycle
project-instructions commands retain their existing current-session rules, and
coordinator-shaped failures still fail closed.

Add a separate recognition path for a successful canonical Task Implementer
`wave-plan`. The hook uses the same trusted adapter boundary to prove that the
command belongs to the current selected lane and that the generation-open
checkpoint is durable. PostToolUse then advances the lifecycle from
`implementation-open` to `reconciliation-required`. A failed command does
nothing, and an already reconciliation-required resume stays conservative
without creating another checkpoint or compatibility path.

Use the same read-only adapter pattern when an exact delegated commit helper
is denied only because the selected lifecycle hook initially resolved the
persistent lane as its Git root. Derive the run from the canonical private
commit evidence, then require the adapter to revalidate the active capacity
batch, running plane, assignment and session digests, exact worker checkout,
authorization or claim path, evidence ownership, command shape, and outer
selected project. Re-run the ordinary commit binding against only that
attested worker root and the command-derived worker session that the adapter
matched to the running plane and canonical authorization or claim. The outer
hook payload session continues to bind direct commits and lifecycle state, but
is not substituted for a nested worker's Task Implementer identity.
Undispatched future tasks have no assignment and are not candidate owners;
arbitrary sibling roots and raw Git remain denied.

#### Selected Option

Use a read-only Task Implementer attestation queried by the lifecycle hook,
while keeping run evidence run-owned and lifecycle state lifecycle-owned.

#### Alternatives Considered

Copying run artifacts into the lifecycle session was rejected because their
embedded absolute paths and digests remain bound to the original private root.
Moving Task Implementer evidence into one transient lifecycle session was
rejected because runs must resume across sessions and retain immutable dispatch
evidence. Broadly allowing external project-instructions commands was rejected
because it would turn a precise adapter into an arbitrary coordinator bypass.
A new public repair action was rejected because the bridge is internal
coordination and users should only repeat `run`.

#### Implementation Boundaries

Task Implementer owns validation of workspace, run, active wave, integration
identity, and run-private paths. `maintain-project-specs` owns helper trust,
current session and selected-lane binding, phase policy, material-write
accounting, and terminal reconciliation. `project-agent-instructions` keeps its
existing schemas and repository mutation semantics. The hook resolves Task
helpers only from explicit canonical installed and sibling-source roots; it
never derives a trust root from the command candidate.

#### Test-First Success Criteria

- TDD-001: The exact run-owned inspect command that the hook formerly denied is
  admitted only for the active integration checkout and run bundle.
- TDD-002: Alternate runs, projects, worktrees, paths, helpers, actions,
  symlinks, unsafe modes, and stale coordinator or wave state remain denied by
  the same fail-closed coordinator boundary.
- TDD-003: The prepared-wave bridge admits inspect and render only during
  implementation-open or reconciliation-required; apply and verify remain
  denied there and use the ordinary terminal seal path.
- TDD-004: A clean integration checkout and an exact staged requirements/design
  pair pass; any other Git delta or unsafe contract path is denied.
- TDD-005: Successful `wave-plan` advances the current selected lane lifecycle
  to reconciliation; failure and read-only Task Implementer actions do not.
- TDD-006: Task Implementer's project-agent contract replay still verifies the
  owner receipt, render state, committed spec blobs, and instruction chain
  before dispatch.
- TDD-007: Contract and metadata tests retain exactly five public actions and
  expose no adapter or lifecycle identifiers to users.
- TDD-008: A real worker commit transaction crosses a coordinator-bound hook
  only for the exact active assignment, command-derived worker session,
  evidence, and worker root even when the hook payload retains the outer
  lifecycle session; future batches, mismatched adapter sessions, wrong roots,
  stale evidence, and arbitrary worktrees fail closed.
- TDD-009: A real-Git sealed-contract adoption followed by correction replan
  authorizes the run-owned lifecycle command only at the exact recorded
  contract head; the original lane base, unattested descendants, and stale
  journal state do not substitute for that identity. A separate real-Git
  correction rejects an independent repository substituted at the integration
  path during replan, prepare, lifecycle authorization, and dispatch.

#### Validation Plan

Run the new cross-hook regression first, then focused lifecycle, Task
Implementer specification, wave, project-instructions, and owner lifecycle
suites. Follow with Python and Markdown lint, stateful skill validation,
changed-scope code and security review, installed parity, and a separately
authorized fresh-session behavior probe.

#### Test Plan

Build one disposable primary checkout, persistent lane, active generation, and
first-wave integration worktree. Exercise the real command shapes through
PreToolUse and PostToolUse, including negative state/path permutations and
failed command responses. Reuse Task Implementer's existing contract replay as
the boundary oracle rather than replacing it with a hook-only assertion.

#### Evaluation Plan

Resume a retained disposable run from the exact pre-dispatch checkpoint after
installation and restart. Observe Task Implementer render and verify its
run-owned decision, dispatch the first worker, complete the run, and
independently confirm the outer lifecycle reaches one current reconciliation
and terminal seal without a user-authored adapter command.

#### Rollout And Rollback

Land lifecycle hook, Task Implementer adapter, tests, docs, and changelog
together. Install both skills and the hook from one reviewed source revision,
verify parity, then restart and review hook trust before the fresh probe. Roll
back the exact installed bundle if admission or reconciliation diverges;
preserve all retained Task Implementer resources and private evidence.

#### Done Definition

One explicit Task Implementer `run` crosses the run-owned integration contract
boundary without weakening ordinary lifecycle evidence, reaches worker
dispatch, and leaves the selected lane lifecycle ready for one final semantic
reconciliation and seal.

<!-- /FEATURE: FEAT-013 -->

<!-- FEATURE: FEAT-014 reqs=REQ-015 status=ready priority=P0 version=1 -->
### FEAT-014: Evidence-preserving active-wave worktree rehydration

#### Requirements Covered

- REQ-015: Recover missing active-wave worktree paths without discarding evidence.

#### Context Evidence

Task Implementer records each internal integration and worker checkout in the
Worktree-owned active lease before and after creation. A stopped run can retain
those rows as `present` and retain locked Git administrative registrations
even when the filesystem directories disappear. Existing `task-recover`
correctly requires the assigned worktree and cwd to exist, while ordinary
worktree creation rejects the retained branch and registration. Worktree
anchor lookup also strictly resolved every registration while locating the
current outer lane, so an unrelated missing internal path blocked even
read-only lease inspection.

#### Design Details

Add one internal coordinator action, `wave-resource-recover`, gated by exact
owning-scope cwd and explicit prior-worker stop confirmation. Under the normal
run lock, revalidate the active run and either its running active capacity
batch or its promotion-pending integrated head. For a running batch, revalidate
assignments and planes, then require one matching `present` resource row for
the current integration and assigned or running worker checkouts. For a
promotion-pending wave, recover only the integration checkout and require its
recorded integrated head.

For an already present path, perform the ordinary linked-worktree identity and
head checks and make no Git change. For a missing path, require one locked
porcelain registration at the exact path and branch. Locate its unique
administrative directory through the common Git directory, reject symlinks and
non-regular `gitdir`, `HEAD`, or index files, require the administrative HEAD
to name the expected branch, require branch tip and registered HEAD equality,
and reject an index lock or staged delta. The integration head is exactly the
contract commit while the wave is running and exactly the recorded integrated
head while promotion is pending. A running worker branch may be exactly its
base or one direct child, matching normal `task-recover`; an assigned worker
stays at base.

After proof, use Git's double-force locked-worktree add form to rehydrate the
same path and branch, journal intent and result, and independently verify exact
head, common directory, named branch, and cleanliness. Re-record only the
idempotent `present -> present` lease state. Return a bounded inventory that
marks restored paths and lost filesystem-only state, while explicitly stating
that task state and promotion did not change. Normal fresh-session
`task-recover` remains the sole running-task ownership transfer.

Change Worktree current-anchor lookup to compare registered paths lexically
before strict resolution, so missing unrelated registrations are skipped while
the one candidate matching the current checkout is still strictly resolved and
fully validated.

#### Selected Option

Rehydrate only exact retained registrations at the current owners' existing
boundaries, then reuse normal worker recovery.

#### Alternatives Considered

Marking the lease rows absent would erase evidence for a possible retained
commit. Pruning Git administration or recreating branches would discard the
owner identity needed to prove safety. Manually reconstructing worker changes
would bypass assignment, review, and commit ownership. Teaching
`task-recover` to create its own cwd would mix coordinator resource ownership
with replacement-worker session ownership.

#### Implementation Boundaries

Task Implementer owns run/wave/assignment proof, expected heads, journaled
rehydration, lease tuple comparison, and the recovery result. Worktree owns
outer anchor and lease identity; its lookup fix changes only candidate
selection order. Git owns the retained locked-registration recovery primitive.
Commit adoption, result acceptance, integration, promotion, cleanup, lane
release, installation, and live run execution remain unchanged.

#### Test-First Success Criteria

- TDD-001: Missing locked integration and running-worker directories rehydrate
  at their exact branches and heads, preserve `present` lease rows, report lost
  filesystem-only state, and permit normal fresh-session `task-recover`.
- TDD-002: Staged administrative index state, index locks, broken symlinks,
  wrong lease tuples, unlocked or ambiguous registration, branch drift, and
  invalid ancestry fail before destructive Git operations.
- TDD-003: Present exact paths are idempotent; recovery changes no task plane,
  wave status, result, commit claim, integration, promotion, or lease release.
- TDD-004: A missing unrelated registration no longer blocks strict discovery
  of the current managed outer lane.
- TDD-005: Contract and metadata tests retain exactly five public actions and
  expose no internal recovery command.

#### Validation Plan

Run focused missing-path and delegated-commit real-Git regressions, existing
interrupted-worker tests, full Task Implementer wave and Worktree manager
suites, interoperability tests, Python and Markdown lint, changed-scope code
and security review, and final alignment.

#### Test Plan

Use disposable primary, persistent lane, integration, and worker worktrees with
isolated private state. Delete only checkout directories while retaining Git
administration and lease rows. Cover clean base, one direct child, staged
index, index lock, broken symlink, wrong path/branch/kind/state, missing or
unlocked registration, idempotent replay, partial restoration, fresh worker
transfer, and unchanged source/lane heads.

#### Evaluation Plan

After separately authorized installation and fresh-session activation, apply
the owner command to a disposable retained run with the observed failure
shape. Independently inspect the registration, branch, head, lease, task plane,
and promotion state before allowing a fresh worker to call `task-recover`.

#### Rollout And Rollback

Land Task Implementer, Worktree, lifecycle hook, tests, specs, docs, and
changelog together. Install the cooperating skills and hook from one reviewed
source revision, verify parity, restart, and use a disposable probe before any
retained real run. Roll back the exact installed payload if activation fails;
do not prune or alter retained run resources.

#### Done Definition

A stopped retained run can restore only its exactly proven active-wave
checkouts and continue through normal fresh-worker recovery without pretending
that lost filesystem-only edits, a commit, or a promotion survived.

<!-- /FEATURE: FEAT-014 -->

<!-- FEATURE: FEAT-015 reqs=REQ-016 status=ready priority=P0 version=1 -->
### FEAT-015: Revision-to-contract impact ledger

#### Requirements Covered

- REQ-016: Prove every accepted prompt revision's complete contract and execution impact before workflow progression.

#### Context Evidence

Task Implementer and Agentic SDLC already snapshot editable prompts into
immutable revisions with exact-byte and normalized-intent identities. Their
refinement state binds the accepted revision, but current readiness checks can
accept unchanged requirements bytes after a semantic edit and Task
Implementer can settle steering as `no_effect` while retaining a coordinator
planned from an older revision. Canonical spec validation proves document
structure and traceability, not that every statement extracted from one prompt
revision was classified faithfully. A self-hash in the editable prompt would
be circular, caller-stale, incompatible with the strict prompt schema, and
would not close that semantic gap.

#### Design Details

Add one internal pure impact validator owned by `maintain-project-specs` and
thin persistence adapters in Task Implementer and Agentic SDLC. The validator
accepts the current canonical spec receipt and record inventory plus bounded
refinement metadata. It enumerates a canonical multiset of statement
occurrences and requires exactly one disposition for each occurrence:

- `changed_contract`, mapped to current requirement and design record IDs;
- `existing_contract`, mapped to current active record IDs;
- `execution_only`; or
- `non_contract`, with a bounded reason such as `workflow_directive`,
  `duplicate`, or `clarification_context`.

The validator rejects omitted or duplicate occurrence keys, inactive or
unknown IDs, mismatched spec identities, unsupported reasons, caller-supplied
aggregate drift, and any ambiguous classification. It derives the effect set
and plan action rather than trusting them. `existing_contract` and
`non_contract` may contribute to a proven no-effect settlement;
`changed_contract` requires current owner-validated contract mappings and, when
it supersedes an earlier spec receipt, a new owner spec transition;
`execution_only` changes the execution basis without pretending the canonical
project contract changed.

Each workflow stores append-only private attempts below its run or SDLC state,
with an atomic current ledger. An attempt binds schema version, workflow,
canonical prompt identity and revision, prompt intent identity, refinement
identity, statement-set identity, exact canonical spec receipt and requirements
and design identities, prior accepted impact head, dispositions, derived
effects, derived plan action, and attempt generation. It contains no prompt or
statement text. Repeating the same validated inputs adopts the existing
attempt. Different inputs create a superseding attempt, and compare-and-swap
publication prevents concurrent writers from replacing a newer accepted head.
An interrupted write is recoverable because the immutable attempt precedes the
ledger update. Publication is serialized by one owner-only per-run lock and
rechecks the observed ledger before replacement. If a crash leaves a different
immutable attempt at the next generation, the owner preserves it as an orphan
and publishes the accepted chain at the next unused generation. Steering is
resolved only after the accepted ledger head is observable.

Treat the canonical spec receipt in an impact attempt as an immutable baseline,
not a digest that every future implementation reconciliation must match.
Legitimate later spec evolution records an append-only spec transition binding
the prior and next owner receipts, the prior impact head, and the bounded
owner-reconciliation reason. The new impact receipt contains that transition,
and its plan-basis receipt binds the transition identity. This prevents routine
post-implementation reconciliation from invalidating history while still
rejecting unexplained spec drift.

Extend workflow state with separate `latest_settled_revision` and
`plan_basis_revision` identities plus the accepted impact and spec-transition
heads. Store them in one separate plan-basis receipt bound to the existing Task
coordinator plan digest or Agentic feature plan digest. Every boundary that can
create resources, dispatch work, integrate, promote, or finalize verifies that
receipt against the latest accepted impact, current canonical spec bytes, and
the unchanged plan identity. This avoids duplicating private identities through
assignment and result schemas while still preventing either artifact from
advancing under a stale basis. A proven later no-effect revision may keep the
older plan basis while advancing the settled revision. Any contract or
execution effect blocks new dispatch and promotion until a safe-boundary
replan settles the new plan. Already-running resources remain retained and are
never reset merely to satisfy migration.

Add the new private impact and plan-basis schemas without keeping an old
progression path. A non-terminal pre-impact run enters a single
forward-reconciliation state that freezes progression until exact historical
inputs are reconstructed or the workflow safely replans with a different plan
identity; an existing coordinator with no basis receipt cannot relabel its old
plan as current material evidence. It does not fabricate a historical receipt.
Terminal records remain read-only and are reported as `historical_no_receipt`.

Expose a sanitized run-status projection in each workflow. It includes only
the public prompt reference/revision, editable snapshot match, semantic-change
state, effect category, public requirement/design IDs or bounded reason class,
and plan action. Raw and normalized hashes, statement identities, free-form
rationale, private impact/run paths, and prompt content remain private; the
existing editable prompt path remains available to the prompt-list workflow.

#### Selected Option

Keep existing automatic digest and immutable-revision detection, and close the
semantic boundary with owner-validated statement coverage, append-only impact
attempts, explicit spec transitions, and downstream basis binding. Do not add
metadata to editable prompt files or introduce a second execution action.

#### Alternatives Considered

Embedding a whole-file hash in the prompt is circular and cannot prove that
requirements or design cover the edit. A user-maintained body hash can be
stale or forged. Comparing only requirements or design bytes detects drift but
not omitted meaning. Letting either workflow independently interpret spec
coverage would create two competing semantic owners. Rebinding or resetting an
active coordinator for every proven no-effect edit would discard valid work
without improving traceability.

#### Implementation Boundaries

`maintain-project-specs` owns the internal schema, canonical spec inventory,
and pure validation rules. Task Implementer owns run-local attempts, steering
settlement, coordinator plan-basis binding, progression gates, migration, and
status projection. Agentic SDLC owns equivalent feature-plan binding,
progression gates, migration, and status projection. Canonical project specs,
public workflow actions, prompt-v3 files, explicit `run`, and lifecycle
project-instructions ownership remain unchanged.

#### Test-First Success Criteria

- TDD-001: Both workflows detect a semantic manual edit from existing prompt
  identities without modifying the editable prompt file.
- TDD-002: Complete valid occurrence coverage derives the expected effect and
  action; omissions, duplicates, inactive IDs, nonexistent IDs, aggregate
  mismatch, receipt drift, and ambiguous material classifications fail closed.
- TDD-003: Append-only publication is idempotent for identical input, creates a
  superseding attempt for changed input, survives interruption before ledger
  publication, preserves and skips a conflicting orphan, rejects a stale
  ledger compare-and-swap, and resolves steering only after the accepted head
  exists.
- TDD-004: A later no-effect revision retains an older plan basis only with
  proven coverage; contract or execution effects block new dispatch and
  promotion until safe replanning binds the new impact and spec-transition
  heads.
- TDD-005: Task coordinator and Agentic feature plan-basis checks reject stale
  prompt, impact, spec-transition, plan, or canonical spec identities before
  downstream progression.
- TDD-006: Non-terminal pre-impact state freezes for forward reconciliation;
  terminal historical state remains readable without a fabricated receipt or
  legacy writer.
- TDD-007: Public status reports useful revision, effect, record-ID, and action
  information while omitting prompt text, private paths, rationale, and all
  prompt-derived digests.

#### Validation Plan

Run the shared validator suite, focused Task Implementer and Agentic SDLC
prompt/workflow suites, lifecycle owner tests, syntax and lint checks,
adversarial security review, changed-scope code review, and final alignment.

#### Test Plan

Use disposable workspaces with multi-statement refinements, active and
superseded spec records, concurrent publication, forced crash boundaries,
multi-revision no-effect and effect chains, active coordinator migration,
terminal history, spec transitions, stale downstream evidence, and sanitized
status snapshots for both workflows.

#### Evaluation Plan

Reproduce a changed editable prompt with existing requirements and design.
Verify that an unsubstantiated no-effect disposition cannot unlock either
workflow, that complete mappings retain or replan deterministically, and that
status explains the outcome without exposing content-derived identities.

#### Rollout And Rollback

Land the owner validator, both adapters, schema migration, tests, specs, and
documentation together. Validate source before any separately authorized
installation. Rollback preserves append-only private attempts and active
resources; a downgraded runtime must refuse new progression rather than write
an older schema.

#### Done Definition

Every accepted prompt revision has complete, owner-validated impact evidence
before either workflow progresses, no semantic edit can be hidden by an
unsubstantiated no-effect label, stale downstream evidence fails closed, and
users receive a concise sanitized explanation without prompt-file hash
ceremony.

<!-- /FEATURE: FEAT-015 -->

<!-- FEATURE: FEAT-016 reqs=REQ-017 status=ready priority=P0 version=1 -->
### FEAT-016: Sealed promotion-review correction round

#### Requirements Covered

- REQ-017: Correct a reviewed wave without promoting known-bad integration.

#### Context Evidence

Task Implementer already prevents coordinator-authored product fixes and
requires combined validation and review before promotion. Its existing
`wave-replan` transition can replace only a resource-free planned tail or add a
tail after a fully promoted and cleaned wave. A blocker reproduced while a
wave is `promotion_pending` therefore has no safe transition: promoting first
violates the review gate, while rebuilding or copying worker changes violates
immutable task and Git evidence.

#### Design Details

Extend the existing internal `wave-replan` owner action with one bounded mode
for an exact clean `promotion_pending` wave. Treat the current integrated head
as a sealed correction base while retaining the wave's original `base_commit`
as the only persistent-lane promotion base. Require all existing task planes to
be merged, all capacity batches done, no active batch, an exact clean
integration branch at the sealed head, and a clean lane at the original base.

Read correction contracts from newly appended handoff task IDs that are absent
from the coordinator index. Reject changes to any indexed pending task,
dependencies on future indexed waves, and correction graphs that require more
than one dependency wave. Extend Worktree generation claims before state,
append the correction task records and capacity batches to the active wave,
create ordinary planned task planes, advance the plan digest, and move the
retained wave back to `preparing` without creating or replacing its integration
worktree.

Dispatch correction workers through the normal assignment, start, heartbeat,
commit, result, and liveness contracts using the sealed integrated head as the
new `contract_commit`. Historical committed or merged assignments validate
against their immutable task-plane base and a well-formed prior plan digest;
live assignments continue to require the current coordinator digest and
contract base. This permits plan evolution without weakening current-worker
ownership.

Reuse the wave's rendered project-instruction decision only after generating
the authoritative receipt at the correction contract and proving it differs
from the recorded receipt solely by a descendant Git head. Requirements,
design, traceability, validator identity, project identity, repository root,
and every manifest-bound instruction byte must remain exact. Any other drift
requires a fresh owner decision and blocks dispatch.

After correction results commit, reuse ordered wave integration. Original task
commits are already ancestors and remain merged; correction commits are merged
normally. Seal the new integrated head, discard stale evidence by identity,
and require fresh combined validation and review. Promotion then performs the
existing single fast-forward from the original wave base to the corrected tip
and cleans every original and correction worker through the existing exact-tip
proof.

#### Selected Option

Append one correction round to the same unpromoted wave and retain a single
promotion boundary.

#### Alternatives Considered

Promoting the blocked wave and adding a later correction tail would knowingly
violate the review gate. Reconstructing earlier task commits would discard
accepted evidence. A separate correction wave would require cross-wave carry
state, a second promotion base, and multi-wave cleanup without improving the
single retained-integration use case.

#### Implementation Boundaries

Task Implementer owns the private handoff, coordinator, wave, task plane,
assignment, worktree, result, integration, promotion, and cleanup transitions.
Worktree continues to own generation claims and lane promotion primitives.
Workers alone own product-code corrections and their direct-child commits.
Public Task Implementer actions, project specification ownership, lifecycle
sealing, and source-lane integration remain unchanged.

#### Test-First Success Criteria

- TDD-001: Exact promotion-pending state appends only new correction tasks and
  leaves the lane and sealed integration head unchanged.
- TDD-002: Indexed task mutation, future dependencies, dirty or divergent Git
  state, incomplete batches, and multi-wave correction graphs fail closed.
- TDD-003: Correction assignments use the sealed integrated head while prior
  merged assignments continue to validate against their original task planes.
- TDD-004: Re-integration retains all original commits, adds correction commits
  in order, invalidates stale evidence by head identity, and promotes once from
  the original base only after fresh validation and review.

#### Validation Plan

Run the focused correction cases, complete Task Implementer wave and contract
suites, canonical spec validation, Python lint and formatting, Markdown lint,
changed-scope code and security review, installed byte-parity checks, and a
retained disposable correction round before using the transition on real run
evidence.

#### Test Plan

Use isolated repositories, Codex homes, persistent lanes, and real linked
worktrees. Cover exact success, stale Git state, incomplete prior batches,
indexed-plan mutation, future and multi-wave dependencies, capacity batching,
plan-digest advancement, historical assignment replay, current-assignment
strictness, interrupted state publication, correction commit validation,
ordered re-integration, stale evidence, promotion, and cleanup.

#### Evaluation Plan

Retain a disposable `promotion_pending` wave with a reproduced blocking review
finding, append one new correction task, and observe the ordinary worker create
and report its direct-child commit from the sealed integration head. Verify the
persistent lane remains at the original base until fresh combined evidence
passes and one fast-forward promotion completes.

#### Rollout And Rollback

Land source, focused state-machine tests, specs, docs, and changelog together;
validate and install Task Implementer as one byte-matched bundle before using
the transition on retained evidence. Rollback the installed source only before
opening a correction round; once state advances, retain it for a compatible
runtime rather than rewriting private coordinator or Git state.

#### Done Definition

A combined-review blocker can be corrected by fresh isolated workers without
promoting known-bad code or rewriting accepted history, and the lane moves only
once to the newly reviewed integration head.

<!-- /FEATURE: FEAT-016 -->

<!-- FEATURE: FEAT-017 reqs=REQ-018 status=ready priority=P0 version=3 -->
### FEAT-017: Digest-bound Task Implementer resume control

#### Requirements Covered

- REQ-018: Resume interrupted Task Implementer runs from authoritative state.

#### Context Evidence

Task Implementer already persists coordinator-v7, wave-v4, task-plane-v5,
assignment-v7, result-v3, incoming-handoff-v1, and interop-v4 artifacts and
uses narrow locks around private transitions. Repeated public `run` is intended
to reuse those artifacts, but recovery decisions are currently distributed
across agent instructions and transition-specific checks. Git intent journals
are append-only audit evidence rather than a parsed recovery authority, and
human `handoff.md` status can lag coordinator, wave, task, lease, and Git truth.

#### Design Details

Keep the five public actions and coordinator-v7 schema unchanged. Add one
private `resume-control-v1` sidecar per run. It stores a monotonic epoch, an
observed-state digest, one intended coordinator transition, its expected
effects, canonical behavior arguments plus their digest, a phase from intent
through effect observation and state/projection commit, and the terminal
digest. Atomic writes sync both the replacement file and its parent directory.
An interrupted intent replays only its stored operation and arguments. An
effect-observed or state/projection-committed record advances validation and
projection without rerunning the mutation. A nonterminal record must be
reconciled before another transition can begin; existing JSONL journals remain
supporting audit evidence.

Add a pure private resume planner that validates manifest and revision
identity; every indexed coordinator, wave, task, assignment, result, and
incoming-handoff artifact; Worktree interop and the external generation and
lease; Git refs, registrations, worktree identity, heads, and index state; and
the relevant project lifecycle binding. It returns exactly one of `execute`,
`wait`, `requires_confirmation`, `blocked`, or `complete`. Only `execute`
contains an opaque single-use token binding the state digest, epoch, and exact
next private transition. The executor reacquires the scope, Git, and Worktree
  locks, recomputes the token, and holds a private execution lock plus the
  owning reentrant scope lock through the transition and projection commit. It
  returns a stale-plan result for replanning if anything changed.

Split passive observation from the existing managed-state reconciliation so a
resume plan cannot mutate local interop while inspecting external truth. Permit
automatic repair only when immutable and live evidence proves one exact state.
Fresh workers wait. Confirmed-stopped pre-start workers rearm once; stopped
workers with validated scoped state use normal recovery; uncertain workers
require confirmation. Missing registered worktrees use the existing retained
resource recovery only after exact lease, branch, head, cleanliness, and
assignment proof. Result, integration, promotion, cleanup, release, and queue
splits finish the single recorded transition without repeating its external
effect.

For existing journal-less coordinator-v7 runs, require a one-time private
adoption receipt bound to the immutable inputs, every current plane, interop
generation and lease, lane head, and retained resources. Stable boundaries are
adopted without changing workflow identity. Partial or ambiguous legacy state
is retained and blocked. Coordinator-v1 through v6 remain unsupported; the
adoption path is current-schema recovery, not a legacy execution shim.

Define the coordinator plan digest as the hash of the complete ordered
`waves[*].tasks` index after every replan. Retained done waves and replacement
correction waves therefore share one combined identity. Add a separate private
current-v7 owner repair for the historical writer signature where the stored
coordinator and prompt-impact basis digests equal only one deterministic
`wave-r` plus first-eight-digest-characters replacement suffix. The caller
supplies both the
old and expected combined digests. Under the project-scope lock, revalidate the
full index, completed prefix, replacement IDs, wave/task planes, active-wave
membership, prompt-impact basis, and absence of assigned or running suffix
workers. Write a fixed-identity recovery journal, publish the plan basis first,
then the coordinator, and finish the journal; every interruption point may
repeat safely. Reject an existing resume controller, changed or ambiguous
state, symlinked or non-file control artifacts, and all older coordinator
schemas. Create the first recovery intent exclusively and keep the repair out
of discoverable helper help. The repair changes no task plane, worker identity,
Git, Worktree, lease, promotion, or public workflow state. Compute an
unchanged-replan comparison from the retained prefix plus candidate tail while
continuing to derive deterministic replacement IDs from the tail digest, so a
second identical replan returns without rewriting or blocking its own wave.

Before creating the recovery journal, require the ready requirements-refinement
contract to match the current managed requirements. Publish its current prompt
impact through the existing append-only CAS ledger, require `retain_plan`, and
bind the resulting impact digest in the fixed recovery identity. This admits a
canonical spec-receipt or non-managed-byte refresh after the Task owner has
updated the compiled requirements binding, while a stale refinement or material
impact still fails before plan mutation. Construct the new plan basis from the
validated current impact and combined plan digest, then preserve the existing
basis-first/coordinator-second order. A crash after impact publication is safe
because impact publication is itself exact and idempotent; a replayed recovery
must observe the same journal-bound impact digest.

Derive effective status from machine-readable state. Before coordinator
creation, handoff may stage plan input. After coordinator creation, handoff is
a human projection only. Extract unindexed correction tasks into an immutable
  digest-addressed `pending-plan-v1` artifact before consumption, then render only coordinator-owned
status and current-action regions with compare-and-swap digests while
preserving pending tasks, failure history, and narrative bytes. Every
transition and replay reconciles that projection. Completion becomes visible
only after external release, local interop convergence, projection update, and
one queue-activation receipt.

Bind each correction `pending-plan-v1` artifact to the active resume epoch and
token before coordinator publication. If publication stops after staging or
after only some coordinator planes are written, replay consumes the staged
task bytes rather than reparsing changed handoff Markdown. Bind capacity,
dispatch commit, task identity, promotion evidence, explicit stop authority,
and final-alignment evidence through the same canonical argument record.

Return command-bearing worker data as transient structured contexts. Arm and
rearm expose one canonical `start_lease`; start returns the exact assignment
scope cwd plus canonical commit and result-publication contexts; recovery
returns the same exact worker scope and recovery argv. Persist only the worker
session fingerprint required for ownership comparison, never the raw session
value. A worker-result publisher accepts one draft from the assignment scope,
validates its commit/tree/path evidence and exact private destination, computes
the stable final digest, and performs one fail-atomic mode-0600 publication.
This removes coordinator path transcription and ad hoc checksum construction
from the execution contract.

Freeze source-derived worker guardrails at the successful `task-start`
boundary. Before start, an assignment must equal the current canonical
guardrail text. After start, the current-v7 task plane's exact assignment
digest, start timestamp, and hashed worker identity attest the accepted
immutable bytes, so later source-only wording growth cannot retroactively
invalidate the running, committed, or merged assignment. Continue to validate
all other task, handoff, Git, Worktree, and lease identities and supply current
transient recovery, commit, and result-publication contexts. Do not rewrite the
assignment, accept unstarted drift, or admit an older assignment schema.

Generated editor workspaces intentionally record a resolved Python executable
rather than a mutable package-manager symlink. Because a Homebrew or equivalent
upgrade may remove that resolved versioned path, public `run` catches only the
exact `VS Code workspace command is unsafe` validation result and invokes the
existing canonical initializer for the same project scope. That initializer
may rewrite only the generated editor workspace when lane and manifest identity
are unchanged; intake then performs full verification again. `workspace reuse`
remains observation-only, and every other mismatch retains the existing
fail-closed behavior.

Route project lifecycle recovery through an explicit owner continuation. Never
simulate hook delivery or depend on Stop generating `UserPromptSubmit`. If
installed source or project instructions require a fresh process, preserve the
run and return the exact reload requirement before further mutation.

Add a hidden current-v7 contract-delta adoption beside digest recovery. It
accepts only a sealed lifecycle state rooted in the current private Codex home,
the exact selected project and lane head, current canonical spec hashes, and a
verified project-instructions target hash. Lane status must contain only
unstaged modifications to requirements, design, and optionally the managed
project instruction file. Journal exact source and integration identities and
file digests before copying those public bytes into the clean retained
promotion-review integration, then create one fixed-message direct-child
coordinator commit. Private lifecycle state never enters Git. Resume and wave
operations may waive external `outer_clean` only while this committed journal,
lane overlay, integration ancestry, and file digests all agree.

Consume a dependent promotion-review correction graph one ready frontier per
integration cycle. The retained wave appends only tasks whose dependencies are
already indexed and merged, dispatches them from the latest integration head,
and leaves later frontiers unindexed until the next promotion-review boundary.
A future planned task may defer, then adopt, a dependency-only change to the
new correction chain; every other field and its resource-free plane remain
unchanged. Before final fast-forward, require the integration target to contain
the adopted contract bytes, journal removal of only those exact lane overlays,
and restore them from the integration commit if promotion does not complete.
This preserves canonical truth across an interrupted cleanup without admitting
general dirty-lane execution.

Project machine task states through one canonical human-status mapping before
writing handoff Markdown: planned is pending, assigned/running/committed/merged
are in progress until their wave promotes, failed is blocked, and only promoted
or cleaned waves are done. Add a hidden current-v7 projection recovery for the
exact historical writer signature. It requires a caller-bound preimage digest,
an idle adopted resume controller, consistent coordinator/wave/task planes, and
one or more unpromoted machine-committed tasks projected as the unsupported
literal `committed`. Journal the exact preimage, task IDs, and canonical
postimage before replacing only those status lines; interrupted publication
adopts the same postimage. Ordinary parsing never accepts `committed` as a
second vocabulary path.

#### Selected Option

Retain coordinator-v7 and add a private digest-bound resume controller, pure
planner, stable-boundary adoption receipt, and machine-owned effective status.

#### Alternatives Considered

Continuing agent-only interpretation leaves cross-file crash boundaries and
stale handoff reporting implicit. A coordinator-v8 hard cut would strand the
preserved current-v7 run. A monolithic automatic repair command would combine
observation and mutation and could act on ambiguous worker or Git evidence.
Public resume flags would expose private state-machine details without
improving the ordinary repeated-run experience.

#### Implementation Boundaries

Task Implementer owns the resume planner, sidecar, adoption, transition
journaling, reentrant scope/execution fencing, machine status, handoff
projection, immutable pending-plan staging, and focused crash harness.
Worktree retains lease, generation, path, and promotion authority. Git retains
ref, worktree, and commit truth. Maintain Project Specs retains lifecycle
transitions and project-instruction receipts. Workers retain product changes
and direct-child commits. No public Task Implementer action or flag changes.

#### Test-First Success Criteria

- TDD-001: Repeated public run against each stable coordinator-v7 boundary
  returns one deterministic outcome and never duplicates workflow identities or
  external effects.
- TDD-002: A journal-less v7 run adopts only after complete state, Git,
  Worktree, lease, and immutable-evidence validation; partial fixtures fail
  closed without mutation.
- TDD-003: A resume token becomes stale when any relevant writer advances and
  the executor replans without applying the recorded transition.
- TDD-004: Crash injection before and after every state write or external
  effect converges to the same terminal identity or one retained blocker.
- TDD-005: Handoff status drift cannot change execution routing and is repaired
  without overwriting pending correction input, failure history, or narrative.
- TDD-006: File sync, parent-directory sync, partial journal, storage, and
  concurrent-restart failures retain a recoverable active intent.
- TDD-007: The documented schema matrix names coordinator-v7, wave-v4,
  task-plane-v5, assignment-v7, result-v3, incoming-handoff-v1, and interop-v4
  consistently while preserving the coordinator-v1 through v6 rejection list.
- TDD-008: Same-token executions serialize across processes; a killed process
  releases its OS locks while retaining the exact nonterminal intent for replay.
- TDD-009: Every controlled CLI argument round-trips through resume state;
  partial correction publication and confirmed recovery replay their original
  immutable input, while committed phases skip the already-applied mutation.
- TDD-010: The aggregate lightweight verifier uses a 900-second bounded
  per-suite default so the measured full linked-worktree crash suite completes,
  while a genuine overrun is still reported distinctly as a timeout.
- TDD-011: The first controlled transition on a new run creates the sidecar
  before its effect under both owning locks; each successful private mutation
  returns an adopted next plan/token, and concurrent bootstrap attempts produce
  only one transition intent.
- TDD-012: A real-Git correction-tail fixture proves the writer hashes the
  retained prefix plus replacement waves, reproduces the prior tail-only
  resume rejection, repeats the unchanged replan without changing coordinator
  or wave bytes, rejects live workers and broken control symlinks, replays
  interruption after either durable repair write without a second identity,
  republishes and binds a spec-receipt-only `retain_plan` impact, rejects a
  stale refinement or changed recovery impact, keeps the owner command out of
  ordinary help, and reaches ordinary resume adoption plus its controlled wave
  preparation afterward.
- TDD-013: A real-Git promotion-review fixture binds an exact sealed lifecycle
  receipt to dirty requirements, design, and provenance-only project
  instructions; commits those bytes once in the retained integration; rejects
  any extra, staged, unsafe, or changed path; and admits resume observation
  only while lane and integration bytes match the adoption journal.
- TDD-014: A dependent correction chain is consumed one ready frontier at a
  time from successive integrated heads, updates a future planned task only by
  adding dependencies on the completed correction chain, and restores adopted
  lane bytes after an injected pre-promotion interruption before converging to
  one clean fast-forward.
- TDD-015: Projection tests cover every machine-to-human task status, repeat
  resume after an unpromoted merged task without parse failure, recover the
  exact current-v7 unsupported `committed` writer output through an interrupted
  journal, and reject changed Markdown, active resume intent, or inconsistent
  planes without changing the handoff.
- TDD-016: A source-only canonical guardrail extension rejects a still-unstarted
  assignment, while the same extension after successful `task-start` preserves
  exact-digest watch and recovery routing without mutating the assignment or
  accepting an older schema.

#### Validation Plan

Run focused resume, adoption, projection, durability, and concurrency tests;
the complete prompt-workspace, task-wave, task-execution, spec, and Worktree
interop suites; skill structure validation with the stateful profile; Python
and Markdown lint; changed-scope code and security review; and final project
alignment. Source validation, installation parity, hook registration and
trust, restart, and fresh-session behavior remain separate evidence gates.

#### Test Plan

Use disposable Git repositories, linked worktrees, isolated Codex homes, and
fresh subprocesses. Inject failures around intake, snapshot, refinement,
checkpoint, generation open, plan and correction publication, resource
creation, dispatch, worker start and result, ordered merge, promotion, cleanup,
lease release, local interop reconciliation, handoff projection, queue
activation, and lifecycle continuation. Assert stable run/revision/plan/task
identity, one active generation and lease, immutable completed evidence, an
unchanged source checkout, no duplicate Git effects, and retained ambiguous
resources.

#### Evaluation Plan

Adopt and resume a disposable journal-less v7 run shaped like a retained
promotion-pending workflow, then crash and restart each recorded transition.
After source validation and separately authorized installation, repeat the
probe in a fresh Codex session before resuming any preserved long-running run.

#### Rollout And Rollback

Land source, schemas, focused tests, specs, docs, and changelog together without
modifying existing private runs. Validate source first. Install only through a
separately authorized provenance path and verify byte parity, registration,
trust, restart, and a disposable fresh-session replay. Once a run writes
resume-control state, use forward repair with a compatible runtime rather than
running an older source over that run.

#### Done Definition

Repeating public `run` after any tested interruption either advances exactly
one proven transition, waits or requests the missing proof, blocks without
mutation, or reports exact completion; machine and human status agree, no
completed identity or external effect is duplicated, and unsupported legacy
coordinators remain rejected.

<!-- /FEATURE: FEAT-017 -->

<!-- FEATURE: FEAT-018 reqs=REQ-019 status=ready priority=P0 version=3 -->
### FEAT-018: PydanticAI-first portable agent application architecture

#### Requirements Covered

- REQ-019: Select the least-agentic sufficient architecture and use a PydanticAI-first portable runtime without surrendering application-owned controls.

#### Context Evidence

`ai-stack` already applies the least-agentic ladder, typed boundaries, hard
gates, replaceability, and conditional infrastructure. Its existing vendor
mapping, however, treats Pydantic AI mainly as the non-OpenAI agent option and
maps OpenAI-native agents directly to a provider runtime. That fragments the
default application model and understates Pydantic AI's current direct model,
provider profile, capability, Agent Spec, Harness, durable execution,
instrumentation, MCP, and evaluation surfaces.

Current official Pydantic AI documentation describes model-agnostic access
over provider SDKs, a direct model request API, typed agents and dependencies,
model profiles, capabilities, MCP, fallback and concurrency primitives,
declarative Agent Specs, optional Harness specialists, durable workflow
integrations, OpenTelemetry instrumentation, and Pydantic Evals. Current
OpenAI Agents SDK and Anthropic managed-agent documentation describe
higher-level provider harnesses, supporting their classification as
specialized runtimes rather than ordinary model adapters.

#### Design Details

Keep direct calls, deterministic workflows containing model calls, and agents
as separate capability-level choices. A direct call describes one-request
control flow, not a prohibition on the Pydantic AI `Agent` primitive. For
portable Python direct calls, use the low-level direct model API when the
caller needs `ModelResponse` control, or a no-tools single-request `Agent` when
typed output parsing, validation retries, typed dependencies, or similar
request services are required. Use a Pydantic AI model-directed `Agent` loop
for portable agent behavior. Intentionally provider-specific task APIs remain
valid for specialized direct workloads. Provider-specific model settings are
adapter-owned extensions and must not change domain tool contracts or
authorization semantics.

Put Pydantic AI behind a small application-owned `AgentRuntime` facade for
run, stream, resume, cancel, and normalized events. Treat the facade as a
logical library boundary by default, not an automatically separate platform
service. The application owns canonical state, identities, prompts and agent
versions, tools, authorization, budgets, routing governance, telemetry,
evaluation, and release records. Pydantic AI owns portable provider
translation and the model/tool interaction loop. Resume and cancel accept
typed mutation requests containing verified principal and tenant context, run
identity, expected state version, and idempotency identity. Resume additionally
binds the exact approval receipt or external event when applicable; the facade
reauthorizes and uses compare-and-swap state transitions.

Use Pydantic AI `Model`, provider implementations, and resolved model profiles
as the execution contract. Supplement them with platform-owned functional,
operational, and governance metadata. Hard policy filters run before scoring,
and the complete primary/fallback route plan is persisted. Do not build a
parallel custom provider/message/tool protocol unless a proven cross-runtime
contract requires it.

All meaningful tools pass through typed domain APIs or a tool gateway that
enforces authorization, approval, idempotency, audit, time and output bounds,
and side-effect rules. MCP remains an allowlisted interoperability boundary.
Routing, retry, and fallback have distinct owners; fallback occurs only for
classified replay-safe failures and never repeats an irreversible side effect
without durable idempotency evidence.

Pydantic AI capabilities and Harness are conditional options for portable
specialists that need reusable context, tools, lifecycle hooks, or delegation.
Each specialist receives a bounded schema, toolset, security scope, cost and
step budget, and evaluation suite. Codex remains the preferred coding-focused
specialist when its coding harness is the requirement. OpenAI Agents SDK,
Anthropic managed agents, and other provider-native harnesses are admitted only
through a written escape decision and retain the same platform controls.

Control-flow autonomy remains orthogonal to graphs and durability. A graph or
durable workflow may wrap direct calls, deterministic steps, agents, or native
runtime activities. PostgreSQL, Redis, vector search, object storage,
gateways, workflow engines, Kubernetes, and multi-agent topologies are
conditional components selected only by workload and operational triggers.
When a durable workflow is required, select one qualified owner from Temporal,
DBOS, Prefect, Restate, or an existing engine based on workload and operational
evidence.

#### Selected Option

Adopt Pydantic AI as the default portable Python execution boundary while
preserving the least-agentic ladder and application ownership above it. Add a
focused provider-agnostic runtime reference, then align the skill core,
architecture, provider, baseline, evaluation, metadata, README, source map,
root catalog, project specs, and changelog.

#### Alternatives Considered

A provider-agent-SDK-per-vendor abstraction was rejected because different
harnesses own incompatible loop, state, tool, event, and delegation semantics.
Making the Pydantic AI abstraction the business application model was rejected
because state, policy, and domain logic must remain replaceable. A mandatory
network facade, gateway, durable engine, and four-store persistence topology
was rejected because small applications can satisfy the same contracts
in-process. A lowest-common-denominator model API was rejected because explicit
provider extensions preserve useful differentiated capabilities.

#### Implementation Boundaries

Owned surfaces are `ai-stack/SKILL.md`, its focused architecture, provider,
baseline, compatibility, evaluation, source-map, README, metadata, and eval
references; the root README; canonical specs; and the `[Unreleased]`
changelog. No provider, service, infrastructure, credential, or production
mutation is part of this source change.

#### Test-First Success Criteria

- TDD-001: A provider-neutral direct request uses the Pydantic AI direct API
  for low-level response control or a no-tools single-request `Agent` for typed
  parsing, validation retries, or typed dependencies; neither primitive makes
  a known sequence agentic.
- TDD-002: Portable OpenAI, Anthropic, Google, and compatible-model agent
  scenarios select Pydantic AI by default and keep provider settings explicit.
- TDD-003: A native runtime is selected only when a named harness feature is a
  requirement and remains behind the runtime, state, policy, and tool facade.
- TDD-004: Router tests separate hard eligibility from scoring, persist route
  reasons, and distinguish pre-execution routing from replay-safe fallback.
- TDD-005: Tool tests prove model-visible adapters cannot bypass authorization,
  approval, idempotency, output bounds, or exact approved arguments.
- TDD-006: A portable specialist uses bounded Pydantic AI capability or Harness
  contracts; a coding-harness requirement may select Codex explicitly.
- TDD-007: Small-app scenarios keep state and the runtime facade in-process;
  scale, durability, retrieval, or team boundaries trigger extra components.
- TDD-008: Provider-side sessions, caches, and compaction are optional
  optimizations and never the sole canonical workflow representation.
- TDD-009: Resume and cancel reject a wrong tenant or principal, a stale
  expected state version, a duplicate or conflicting idempotency identity, and
  a mismatched approval receipt before changing canonical run state.

#### Validation Plan

Run focused skill-structure and project-spec validation, Markdown lint, static
trigger and metadata inspection, changed-scope review, security review, and
final cross-surface alignment. Verify volatile vendor claims from current
official Pydantic, OpenAI, Anthropic, Google, MCP, and durable-runtime sources.

#### Test Plan

Add representative scenarios for portable direct access, a portable agent, a
provider-native escape, a bounded specialist, router/fallback separation, tool
authorization, and small-to-large platform escalation. Check for stale
provider-native defaults, duplicated provider abstractions, mandatory optional
infrastructure, missing source-map links, and README or metadata drift.

#### Evaluation Plan

Walk a multi-provider application with read tools, side effects, approvals,
durable waits, and specialist agents through the decision tree. Confirm the
portable path can change providers without changing domain contracts, native
escapes remain deliberate, and all selected optional infrastructure has an
owner, trigger, acceptance gate, and rollback plan.

#### Rollout And Rollback

Land source documentation, metadata, evals, specs, catalog, and changelog in
one reviewable change. Roll back the complete aligned change if static
validation or official-source review finds a contradiction; do not retain a
partial vendor mapping.

#### Done Definition

The skill selects the least-agentic sufficient architecture, uses Pydantic AI
as its portable Python default, preserves differentiated provider features,
keeps application controls and state outside provider runtimes, justifies every
specialist or native escape, and distinguishes source readiness from installed
or fresh-runtime behavior.

<!-- /FEATURE: FEAT-018 -->

<!-- FEATURE: FEAT-019 reqs=REQ-020 status=ready priority=P0 version=2 -->
### FEAT-019: Concise zero-write Task Implementer lane status

#### Requirements Covered

- REQ-020: Preserve crash-safe completion evidence while projecting concise persistent-lane and current-run progress without file disclosure or mutation.

#### Context Evidence

`prompt_workspace_reporting.lane_report()` currently computes a full
source-to-lane Git comparison and returns every changed path. The generated
`Task Implementer: Show Pending Lane Changes` task forces `--json`, while the
generic emitter serializes the complete nested payload on one terminal line.
The same function already reads the Worktree lane anchor, generation receipts,
interop state, sealed summaries, and safe Git cleanliness through
`--no-optional-locks`.

Coordinator-v7 owns the immutable wave plan and machine run status. Wave-v4
owns task indexes, active batches, promotion heads, and cleanup state, while
task-plane-v5 owns assignment, dispatch, heartbeat, result, and task state.
The canonical human projection already treats planned work as pending,
assigned/running/committed/merged work as in progress, failed work as blocked,
and tasks as done only after their wave promotes. Worktree lane state owns
released, integrated, pending, and active generation identity.

Existing lease-resource inspection is not a strict read: its Worktree path
re-records manifest lease state. The scope lock may create or chmod its file.
Neither is suitable for a zero-write status command. All relevant private JSON
writers use atomic replacement, so two matching complete observations can
prove a stable read without acquiring those mutation-adjacent interfaces.

#### Design Details

A narrow Task Implementer reporting module continues to own `run-summary-v1`,
source observations, finalization, handoff rendering, and lane inspection.
Completion evidence and its existing detailed change statistics remain
unchanged. Lane visibility becomes the separate hard-cut
`task-implementer/lane-report-v2` projection and never embeds a sealed summary
or invokes Git diff/statistics parsing.

Select exactly one current run from validated run, interop, and finalization
state. If none is active, use only the latest sealed pending summary; if no
current coordinator or sealed summary exists, render planning or unavailable
totals explicitly. Never aggregate legacy generations whose immutable work
totals do not exist. Lane generation totals remain independent of current-run
task, worker, and wave progress.

Derive task and worker counts from task-plane-v5 with wave-promotion precedence.
One indexed task is one planned logical worker slot. Assignment creation,
dispatch arming, running, accepted result, failure, and supersession map to
created, queued, active, finished, failed, and superseded worker counts. A task
becomes promoted only when its wave has a valid promotion head; committed or
merged tasks remain in progress. Wave totals and active identity come from the
coordinator index, and promotion totals come from validated promotion heads.

Observe the anchor and every selected private artifact twice, including the
complete relevant file set and stable byte digests. Emit only when both bounded
observations match and cross-file identities validate. Retry once; continued
movement returns `WORKSPACE_BUSY` without partial output. Do not acquire the
scope lock or call live lease-resource inspection. Use the existing bounded
`--no-optional-locks` Git wrapper only for source/lane cleanliness needed by
the next-action decision.

The generated workspace task becomes `Task Implementer: Show Lane Status`,
invokes the human renderer by default, clears and reuses a dedicated terminal,
suppresses command echo and reuse messaging, and does not steal focus. Explicit
`--json` returns the same concise v2 data. The current four-task generated
document is the sole previous shape accepted for migration; older shapes and
all arbitrary differences fail closed.

#### Selected Option

Use one derived lane-report-v2 read model over existing authoritative schemas,
with optimistic two-pass consistency and separate human and JSON renderers.
Keep run-summary-v1 and all mutation owners unchanged.

#### Alternatives Considered

Filtering only the terminal rendering was rejected because the command would
still compute, retain, and expose the large file-level payload to JSON callers.
A new public status action was rejected because it would expand the exact
five-action interface. Reusing live lease-resource inspection or the scope lock
was rejected because those paths may write metadata. Aggregating every pending
generation was rejected because legacy generations lack immutable totals.

#### Implementation Boundaries

Task Implementer reporting owns snapshot selection, validation, progress
derivation, schema-v2 output, and human rendering. The generated workspace owns
only the task label, invocation, presentation, and exact previous-shape
migration. Coordinator, wave, task-plane, run-summary, Worktree, lease, hook,
and five-action public schemas remain unchanged. No process-liveness or
physical-resource claim is added.

#### Test-First Success Criteria

- TDD-001: Every task/wave state and worker arm/start boundary maps to exact,
  deterministic counts; committed and merged work stays unpromoted.
- TDD-002: Planning, active, blocked, finalizing, complete, legacy-only, idle,
  integrated, and removed lanes render bounded useful status.
- TDD-003: Two matching observations emit one report; changing artifacts or
  anchors retry once and then return `WORKSPACE_BUSY` without partial counts.
- TDD-004: Report execution leaves private files, lock metadata, Git state,
  Worktree lane state, and lease state byte-identical and never calls a diff,
  mutation, or lease-resource inspection helper.
- TDD-005: Human and JSON output contain no change statistics, paths, commits,
  branches, private IDs, prompt content, or embedded summaries.
- TDD-006: The exact current four-task workspace migrates to the human status
  task; reuse requests upgrade and every other difference remains tampering.
- TDD-007: Existing run-summary finalization/replay tests remain unchanged and
  the public interface still contains exactly five actions.

#### Validation Plan

Run focused reporting, workspace, resume, wave, interoperability, Worktree,
lifecycle-hook, and contract tests. Run Python and Markdown lint, changed-scope
review, security review of path/snapshot/zero-write boundaries, skill alignment,
and final cross-surface alignment.

#### Test Plan

Add table-driven projection tests for every state vocabulary and mixed
multi-wave progress. Add real disposable-repository tests for source/lane
cleanliness, pending generation transitions, integration, removal, exact
workspace migration, tampering, concurrent snapshot movement, missing or
unsafe state, serialized forbidden fields, size bounds, and complete before/
after fingerprints of private and Git-owned state.

#### Evaluation Plan

Run a disposable multi-wave Task Implementer workflow and inspect it while
workers are queued/running, after results commit and merge, after each wave
promotes, during finalization, after generation release, after source
integration, and after lane removal. Confirm each report is short, actionable,
and truthful without displaying changed files or interfering with execution.

#### Rollout And Rollback

Validate source and all focused/broad suites first. Install only the updated
Task Implementer skill through the repository installer, verify source/install
parity, and smoke-test a freshly generated or migrated workspace in a fresh
session. Do not install or register hooks because their runtime contract is
unchanged. Roll back lane-report-v2, its renderer, and the generated workspace
shape together; run-summary-v1 needs no rollback or migration.

#### Done Definition

The lane task renders bounded current progress, promoted and remaining work,
generation backlog, and the next public action without changed-file data;
machine and human counts agree; repeated or concurrent inspection is
zero-write and fail-closed; migrations are exact; run-summary-v1 and the five
public actions remain unchanged; and source, installed, and fresh-session proof
are reported separately.

<!-- /FEATURE: FEAT-019 -->

<!-- FEATURE: FEAT-020 reqs=REQ-021 status=ready priority=P0 version=2 -->
### FEAT-020: Incremental lean-skill alignment and evaluation evidence

#### Requirements Covered

- REQ-021: Align skills with lean authoring and target-specific evaluations.

#### Context Evidence

`align-skill` already owns skill authoring, progressive disclosure, review
lanes, and structural validation. Its validator recognizes `evals/` but does
not parse evaluation content, while many existing source skills have no eval
directory. Existing trigger suites use both Markdown and CSV, and the current
real-catalog regression requires basic validation to remain compatible with
that catalog. Official OpenAI guidance makes `SKILL.md` plus name and
description the portable minimum, recommends progressive disclosure,
imperative steps, one focused job, and scripts for deterministic behavior. The
open Agent Skills guidance recommends a lean main file, focused references,
realistic trigger cases, and output comparison against a prior or no-skill
baseline.

#### Design Details

Keep semantic authoring judgment in the workflow and rubric. Review each
description for its job, outcome, trigger intent, routing-relevant operating
mode, and adjacent negative boundary without keyword scoring. Define removable
no-op guidance as text whose deletion cannot change a decision, constraint,
route, validation, safety outcome, or output. Classify workflow blocks as
flexible, bounded-judgment, or deterministic without requiring annotations in
target skills. Keep decision-changing rationale and use exact scripts only
where deterministic reliability warrants their maintenance cost.

Make `evals/trigger-prompts.csv` the single trigger authority for every
authorized writable target that successfully completes alignment. Its exact
columns are `id,should_trigger,prompt`; values are non-empty, IDs and prompts
are unique, labels are lowercase `true` or `false`, and each label has at least
three cases. Near-miss negatives must exercise adjacent routing boundaries.
Broader or ambiguous skills should grow toward roughly twenty cases, but the
validator enforces only the six-case floor.

Add `--require-evals` independently of the existing validation profile. Basic
validation continues to accept a missing eval suite so the source catalog can
migrate only when each skill is next aligned. Any canonical CSV that exists is
validated in every mode. Strict validation requires it, rejects a simultaneous
legacy `trigger-prompts.md`, and does not treat Markdown-only coverage as
canonical. The validator emits a warning above 500 `SKILL.md` lines; the
alignment workflow must reduce the file through progressive disclosure or
record why the remaining core instructions are justified.

Treat the canonical CSV as target-owned input. Before opening it, reject a
symlink at either the `evals/` directory or file component and require the
resolved file to remain beneath the resolved skill root. Report malformed or
duplicate cases by row number only; do not include raw IDs or prompts in
terminal or CI diagnostics.

When that warning fires, or when semantic review finds an overloaded entry
point, load one focused long-skill refactoring reference. Classify every block
as always-needed core, conditional reference material, deterministic scripted
work, reusable asset or example content, removable behavior-neutral or
duplicated prose, or an independently triggered job. Before changing
ownership, record the destination of safety constraints, decision-changing
rationale, non-obvious gotchas, preconditions, failure and retry behavior, stop
conditions, validation, and output contracts. Extracted references stay one
level deep and receive exact conditional read directives; do not summarize
sections mechanically.

Record `SKILL.md` logical lines before and after every long-skill refactor with
the validator-compatible `splitlines()` method. Record token cost only when the
same compatible tokenizer or runner can measure both
versions, including its method; otherwise use `UNAVAILABLE`. Keep both metrics
informational. Do not add a tokenizer dependency, token threshold, or hard size
failure. A target may remain above 500 lines only with a specific explanation
of why the retained blocks are always needed or safety-critical and with
non-regression evidence proportionate to the change.

Store the working-byte baseline only in an owner-only task temporary tree.
After comparison and rollback needs end, remove the exact validated task-owned
tree with scoped cleanup. If policy requires retention, record the reason,
owner-only permissions, cleanup owner, and deadline. Never transmit private
target contents to an external tokenizer or runner without authorization.

Recommend a separate skill only when a block has an independent trigger and
outcome, not merely because the entry point is long. Existing sibling skills
may be aligned when already authorized; creating a new skill continues through
`skill-creator` rather than turning `align-skill` into a second scaffolder.

For material workflow or output changes, use `evals/evals.json` with at least
two realistic cases, expected outputs, optional input files, and concrete
assertions, unless deterministic target tests fully verify the outcome.
Capture the current target working bytes in a task-owned temporary snapshot
before editing so a dirty worktree baseline is not silently replaced by
`HEAD`. Keep generated benchmark workspaces outside the reusable skill folder.

Report evidence as separate lanes: `STATIC_PASS` for schema and definition
checks, `RUNTIME_PASS` for observed fresh-surface routing, `QUALITY_PASS` for
assertion-backed baseline comparison, plus `NOT_RUN`, `UNAVAILABLE`, and
`FAIL`. Run fresh trigger checks after invocation changes when a runnable
surface exists and quality comparison after material behavior changes when a
baseline and runner exist. Missing higher-tier evidence remains explicit; any
executed applicable failure blocks completion.

#### Selected Option

Adopt a target-scoped strict evaluation contract with deterministic structural
validation and semantic rubric review. Preserve default catalog compatibility,
use a soft line-budget warning, and require higher-cost runtime or quality
evidence only when the changed behavior and available runner justify it.

#### Alternatives Considered

A documentation-only recommendation was rejected because aligned targets could
still omit evaluation coverage. Making evals mandatory in basic validation was
rejected because it would break unrelated legacy skills and create a broad
migration. Hard semantic regex checks and a hard 500-line cap were rejected as
easy to game and harmful to complex but justified workflow skills. Mandatory
live benchmarks for every edit were rejected because they add cost without
useful evidence for deterministic or metadata-only changes.

#### Implementation Boundaries

`align-skill/SKILL.md` owns the mandatory target workflow and evidence claims.
Focused references own authoring, evaluation, triggering, and structure detail.
The conditional long-skill reference owns block classification, preservation,
cost measurement, split decisions, and justified exceptions without loading on
ordinary alignment tasks.
The Python validator owns only machine-verifiable CSV structure, strict-mode
presence, target-owned path containment, privacy-preserving diagnostics,
dual-authority rejection, and the line-count warning. Templates own plan/report
evidence fields. The root catalog, changelog, canonical specs, and skill
metadata remain aligned. Installation, global CI enforcement, and
repository-wide eval migration are outside this change.

#### Test-First Success Criteria

- TDD-001: Missing evals pass basic validation and fail strict validation;
  valid three-positive/three-negative CSV coverage passes both.
- TDD-002: Invalid headers or labels, blanks, duplicate IDs or prompts, and
  insufficient label counts fail with actionable messages.
- TDD-003: Markdown-only coverage fails strict validation, dual Markdown/CSV
  authorities fail, and malformed present CSV fails even in basic mode.
- TDD-004: A 500-line `SKILL.md` produces no budget warning, a 501-line file
  does, and warnings do not become validation failures.
- TDD-005: `--require-evals` composes with the stateful-workflow profile and
  the existing real-source catalog still has zero basic validation failures.
- TDD-006: `align-skill` has its own canonical positive and negative trigger
  suite and passes strict and portable structural validation.
- TDD-007: A mixed 700-line quality case requires six-way block
  classification against a concrete line-range fixture, exact conditional
  reference routing, preservation of safety rationale and a rare gotcha, and
  before-and-after size evidence without converting token unavailability into
  failure.
- TDD-008: A safety-critical quality case may remain above 500 lines only with
  a specific core-content justification and non-regression evidence against a
  concrete line-range fixture; it rejects mechanical summaries, length-only
  splitting, and generic complexity claims.
- TDD-009: External file and directory symlinks fail before CSV parsing, and
  duplicate diagnostics report row numbers without reproducing the private
  sentinel ID or prompt.

#### Validation Plan

Run validator fixture tests, strict target validation, default catalog
validation, upstream quick validation, no-write Python compilation, scoped
Ruff and Markdownlint, project-spec validation, code review, security review,
final alignment, and `git diff --check`. Treat source validation, installed
parity, restart, and fresh-session behavior as separate proof levels.

#### Test Plan

Add table-driven temporary-fixture coverage for strict presence, the canonical
CSV schema, label counts, duplicates, blanks, dual trigger authorities, and
500/501-line warning behavior. Exercise strict evaluation together with the
stateful-workflow profile, retain the real-source default-validation
regression, and give `align-skill` at least three positive and three adjacent
negative trigger cases. Keep the existing 500/501 validator boundary as the
complete deterministic test of the generic warning predicate; exercise
700-line classification and justified exceptions as semantic quality cases
rather than duplicate fixtures or brittle keyword checks.

#### Evaluation Plan

Review the canonical trigger cases for realistic positive intent and adjacent
negative routing. If a fresh runnable Codex surface and baseline runner are
available, observe trigger routing and compare material output behavior with
the captured pre-edit snapshot; otherwise record runtime and quality evidence
as unavailable without weakening the static acceptance gate. For long-skill
cases, deterministically protect fixture paths, 700-line counts, and semantic
anchor ranges, then use the captured working bytes as the baseline and compare
preservation, reference routing, exception quality, line counts, and compatible
token cost when exposed by the same runner.

#### Rollout And Rollback

Apply the strict contract only to writable targets selected for alignment. Do
not run the repository installer from a dirty multi-skill tree. If later
authorized from a clean or scoped source, install through the repository-owned
workflow, verify source/install parity, restart only when needed, and then run
fresh-session trigger checks. Roll back the validator flag, canonical eval
files, and aligned guidance together rather than retaining dual formats.

#### Done Definition

`align-skill` makes writable targets lean, bounded, and independently
evaluable; its own strict evaluation contract passes; basic validation remains
compatible with unrelated legacy skills; higher-cost evidence is reported
truthfully; long skills are classified rather than blindly summarized; and no
installation or broad migration is implied by source proof.

<!-- /FEATURE: FEAT-020 -->
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
