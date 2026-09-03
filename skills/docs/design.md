<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001,REQ-005 status=ready delivery=unassessed priority=P0 version=1 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-001 -->

<!-- FEATURE: FEAT-002 reqs=REQ-002,REQ-003 status=superseded delivery=unassessed priority=P0 version=11 -->
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
Current-session evidence exposed a separate guidance mismatch: the installed
hook correctly rejected the skill's repo-relative checkout helper, while the
literal absolute installed coordinator with a trusted absolute interpreter was
accepted. The denial reason did not distinguish that trust mismatch from
missing project-instructions inspect flags.
Session `01a03b98-76cf-7413-ba14-919e9d2669ca` exposed an ownership-continuity
gap after discovery had already loaded the selected-project `AGENTS.md`: an
earlier sealed session held a verified active receipt for the exact target
digest, but the new session inspected only its own empty bundle and therefore
requested redundant exact-digest adoption approval.
Current runtime evidence exposed a separate historical-audit defect: a
workspace with 138 lifecycle sessions and one structural directory occupied
only about 4 MiB, yet `SessionStart` emitted an audit failure because it
materialized all workspace children and rejected more than 128 before project
scope or terminal phase filtering. The message was additional context rather
than a tool or coordinator gate, and no records were moved or archived.

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

Runtime examples expose the trust contract directly: use one literal absolute
trusted Python path and the literal absolute installed coordinator discovered
under the user skills root. Do not advertise repo-relative checkout helpers,
shell variables, substitutions, symlinks, or basename lookalikes. A malformed
coordinator denial names the installed-helper mismatch separately from the
additional absolute bundle and `--codex-home` bindings required by
project-instructions inspect. The hook remains fail-closed; this is a guidance
and diagnostic correction, not an alternate trust path.

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

Keep ownership continuity inside `project-agent-instructions`, the owner of the
managed region. Give each Git-workspace bucket one dedicated owner-only shared
private root containing a bounded exact-schema ownership registry and one
advisory lock. The registry maps a canonical digest of project, Git root, scope,
and target path to a monotonic generation, active/pending/blocked/retired
status, exact target digest, optional active or retired receipt, and optional
sealed or completed-apply source-state digest. First use builds a complete
generation-zero registry in a
private sibling directory and atomically renames that directory into place, so
no caller can observe an initialized but incomplete root. Inspect reads an
existing entry as authoritative and never falls back when the registry is
initialized but missing, unsafe, malformed, or mismatched. Apply acquires the
shared lock before authority and registry preflight, then holds it across target
mutation, final state, conditional registry publication, and retained
recovery-evidence release. A stale active writer therefore cannot publish after
retirement, and publication failure leaves the transition blocked and
recoverable.

For workspaces created before the registry, permit one fail-closed bootstrap.
Scan sibling lifecycle sessions only as an unordered evidence set. Validate the
complete sealed lifecycle, exact regular mode-safe final state, and exact
regular mode-safe receipt for every relevant same-scope event. Bootstrap only
when at least one active event exactly matches the current whole target and all
relevant valid events agree on the same active authority. Any retirement,
different generation, malformed or unsafe relevant evidence, or ambiguity
blocks bootstrap. If the scan instead finds exactly one completed active apply
whose registry publication was interrupted, publish a pending generation bound
to its exact receipt, whole-target digest, and state digest. Other sessions
cannot import it; only the matching writer can promote it to active. Never
infer causality from a session ID, timestamp, directory
order, or filesystem metadata. The first failed or empty scan publishes a
receipt-free blocked subject generation; later evidence removal cannot make an
older active event authoritative, while an exact-digest adoption may publish a
new active generation. Marker-only drift remains continuable only from a
receipt already in the current session. If registry lookup or bootstrap does
not satisfy the complete proof, preserve `ADOPTION_APPROVAL_REQUIRED`.

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
ancestor without changing the selected-project target. Continue exact managed
ownership across sessions from a locked monotonic workspace-private registry,
with unanimous unordered sealed history only as a one-time missing-entry
bootstrap; instruction discovery and marker presence remain non-authoritative
for mutation ownership.

Remove the cross-session historical audit from the correctness path.
`SessionStart` loads and reports only the exact current session; reopening an
expired session binds a fresh lifecycle from the canonical repository specs.
A separate best-effort maintenance component owns no semantic project decision
and never injects routine context. It uses stable locks outside movable session
bundles, refreshes non-authoritative activity evidence, and shares the
project-instructions registry validator so missing or pending ownership
authority protects its sealed history. A root cursor rotates one workspace per
start, including inactive workspaces, while workspace-private scan and journal
cursors bound each slice without turning a visit count into retention policy. A
validated candidate is journaled and fsynced, atomically renamed to same-device
staging, then removed by directory-file-descriptor traversal that rejects
links, device crossings, and unexpected identities. Unknown staging remains
protected. The normal age policy removes terminal bundles after 30 days and
unfinished planning, planned, implementation, or reconciliation bundles after
90 days; current and `seal-armed` sessions remain protected. A 1-GiB allocated
storage high watermark activates only from a complete root aggregate and
reclaims oldest-first within each complete workspace pressure slice toward
768 MiB before eligible unfinished state. No archive is created.
Crash recovery deletes a staged ownership-bound bundle only while both the
recorded registry generation and canonical registry digest remain current;
generation or same-generation digest drift and unreadable authority restore the
exact staged inode to its original session path for reclassification. Exact
status validation disables pressure from malformed state, and a rotating
journal cursor prevents malformed prefix journals from starving valid recovery.

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
ownership evidence and weaken the fail-closed boundary. Requiring a new user
adoption approval in every session was rejected because session-private storage
is an implementation detail, not an ownership change. Treating every intact
marker as owned was also rejected because a hand-crafted or copied marker is
not sealed lifecycle proof. Session-ID, timestamp, directory, or file-metadata
ordering was rejected because none is an established strict causal sequence;
it can resurrect an older active receipt after retirement. Unordered sealed
history alone is retained only for unanimous one-time bootstrap. A locked
workspace registry is accepted despite its shared-writer cost because it is the
smallest boundary that can record monotonic revocation and re-adoption without
a perpetual prompt or an unsafe inferred order.
Raising the 128-entry cap or moving an arbitrary oldest set was rejected
because both preserve cross-project coupling and recur independently of actual
storage. Archiving was rejected because no consumer requires historical paths
and it retains the privacy and storage burden. A second active-session index
was rejected because exact current-session state already owns correctness and
another cross-writer authority would add reconciliation risk. Retention age
and allocated bytes are policy; visit limits are only a resumable startup work
budget.

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
result; hooks do not order or perform that repair. The same owner also validates
and imports exact workspace-registry ownership into the current bundle,
bootstraps a missing entry only from unanimous sealed history, and publishes
active or retired generations before recovery evidence is released; the
lifecycle hook, discovery chain, and repository marker do not independently
grant ownership. Private maintenance adds only non-authoritative activity,
root/scan/journal cursors, journal, staging, and aggregate status records under
the workspace
maintenance namespace; it does not change lifecycle or registry schemas and
exposes no public flag or user maintenance workflow. Lifecycle and
project-instructions writers adopt the stable external workspace/session lock
order before cleanup is enabled.

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
- TDD-020: Public inspect, migrate, validate, and project-instructions inspect
  examples use literal absolute interpreter and installed-helper placeholders;
  no repo-relative coordinator form remains. Malformed-command feedback names
  that trust boundary while copied installed-hook tests continue rejecting the
  checkout helper and accepting the installed helper.
- TDD-021: A second session imports an exact active workspace-registry receipt
  and reaches `existing-sufficient` or an owner-authorized refresh without
  adoption approval or an unrelated repository mutation. Missing, stale,
  retired, subject-, target-, or body-mismatched authority is not imported and
  retains `ADOPTION_APPROVAL_REQUIRED`; an unsafe or malformed existing
  registry fails closed without historical fallback.
- TDD-022: Unanimous exact active sealed history bootstraps the shared registry
  once. Any retirement, different generation, invalid relevant evidence,
  same-millisecond reverse UUID order, or clock regression blocks bootstrap and
  publishes a durable blocked subject generation. Removing that conflicting
  evidence cannot trigger another bootstrap or resurrect older ownership. A
  serialized later exact adoption may publish a new active generation that
  supersedes the blocked state or retirement tombstone.
- TDD-023: Shared registry reads and writes reject unsafe mode, links, schema,
  subject, digest, generation, initialized-but-missing state, and
  concurrent-lock drift. A retirement holding the lock rejects a stale active
  writer. Publication failure leaves target recovery evidence or a safely
  retryable created/adopted transition and never reports a sealed success
  without the matching monotonic entry. Two paused first-use initializers expose
  only one complete generation-zero root, and the losing caller continues from
  that valid registry. After a created transition loses registry publication,
  another session records a pending generation rather than a generic block;
  that observer cannot import it, and the exact original receipt/state writer
  can recover it to active. Regular workspace files are ignored during bounded
  legacy bootstrap.
- TDD-024: Repeated inspection after a same-session retirement is
  `not-applicable` while the target remains absent or human-owned; managed
  reappearance remains adoption-gated.
- TDD-025: More than 128 safe terminal, unfinished, sibling-scope, structural,
  and unknown workspace entries do not fail `SessionStart`, inject a historical
  reconciliation request, or delete records by count. Current-session context
  remains exact.
- TDD-026: Age and storage-pressure maintenance deletes only whole eligible
  bundles: terminal after 30 inactive days, unfinished after 90 inactive days,
  and ownership-safe terminal evidence from a complete 1 GiB aggregate toward
  768 MiB. Current,
  `seal-armed`, pending, legacy-required, malformed, unsafe, and unknown state
  remains unchanged; no archive is created.
- TDD-027: Stable workspace/session and ownership locks prevent lifecycle
  writes, apply/bootstrap, and cleanup classification or rename from racing.
  An fsynced journal recovers an exact staged deletion after interruption;
  identity or canonical registry digest drift, links, hard links, device
  crossings, unsafe activity, missing sealed final state, malformed status, and
  unknown residue fail closed without following or deleting them.
- TDD-028: Maintenance is idempotent and bounded across concurrent starts.
  Missing safe activity metadata initializes protection, subsequent session
  activity refreshes it, persisted scan cursors converge, aggregate private
  status explains protected pressure without routine hook context, and an
  expired session re-enters through a fresh planning lifecycle.

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
admission of the revised bundle. Cover exact sealed history bootstrap and
workspace-registry continuity plus every rejected lifecycle, state, ownership,
subject, target, digest, mode, link, retirement, ambiguity, concurrency, and
publication-recovery case, including first-use root publication and blocked
bootstrap replay after conflicting evidence is removed, plus an interrupted
created publication observed before its exact writer retries. Add historical
audit, age/pressure maintenance, activity, stable-lock ordering, ownership
retention, crash staging, unsafe filesystem, bounded cursor, concurrency, and
fresh-expired-session regressions using disposable private roots and patched
small thresholds.

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
In a disposable installation with more than 128 historical entries, confirm
the installed hook emits normal current-session context, maintenance stays
non-blocking, and only eligible bundles are reclaimed. Installation parity,
restart and `/hooks` trust, and a real fresh-session probe remain separate from
source validation.

#### Implementation Evidence

`project_specs_maintenance.py` now owns the private activity, complete aggregate
usage, bounded root, scan, and journal cursors, stable lock, digest-bound cleanup
journal, same-device staging, and
directory-FD traversal contracts. The lifecycle hook imports that exact sibling
module, reports only the current session, refreshes activity, and invokes a
nonblocking maintenance slice. Both canonical coordinator CLIs participate in
the same stable lock order. `workflow.retention_disposition` reuses the
workspace ownership registry and sealed final-state binding; its private runner
exposes no public workflow flag.

Focused source tests cover 140 and 300 historical sessions, inactive-workspace
rotation, complete and partial aggregate pressure, cross-slice oldest-first
terminal ordering, 30/90-day expiry,
`planned` expiry,
current and `seal-armed` protection, missing/future/unsafe activity, hard links,
lock contention, exact status validation, strict journal identity and rotation,
registry-generation and same-generation digest drift restore,
matching-snapshot resume, sealed missing-final-state protection, no archive,
closed ownership dispositions, and
concurrent lifecycle writers. Coordinator, hook, project-instructions, Ruff,
Markdown, structure, and installer tests pass. A disposable install verified
the exact source/installed skill trees apart from installer provenance, all
three hook payloads, and the installed maintenance and retention suites. A
separate disposable hook probe produced normal
current-session context with 140 historical bundles. The active user hook was
not replaced; restart/trust and a real fresh-session activation probe remain
unverified rollout gates.

#### Rollout And Rollback

Install the reviewed skills and hook with recoverable backups only after the
entire affected dirty skill folders are reviewed, restart Codex, review and
trust the changed hook, then use a fresh selected-project session. Cleanup is
disabled until every writer uses the stable lock generation. Restore the exact
backup if the fresh probe fails before accepting further project writes.

#### Done Definition

The lifecycle reaches a receipt-bound terminal state using canonical installed
coordinators, repeated writes stay silently accounted, semantic reconciliation
occurs once at Stop, unchanged specs can seal after current-epoch validation,
invalid dispositions cannot publish render evidence, exactly owned decision
revisions cannot deadlock on their own private rules, unowned changes remain
blocked,
explicit compatibility intent governs the current change and future sessions,
nested selected projects follow effective Codex root discovery without changing
their target scope, sealed managed ownership continues across ordinary sessions
without redundant human approval, unproven markers remain adoption-gated, and
noncanonical commands still fail closed. Historical session quantity no longer
affects startup correctness; safe private state converges under the approved
30-day, 90-day, and 1-GiB/768-MiB policy without archives, loss of protected
recovery evidence, or routine user maintenance.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-002 -->

<!-- FEATURE: FEAT-003 reqs=REQ-004 status=superseded delivery=unassessed priority=P0 version=2 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-003 -->

<!-- FEATURE: FEAT-004 reqs=REQ-005 status=ready delivery=unassessed priority=P1 version=1 -->
### FEAT-004: Layered repair verification and activation

#### Requirements Covered

- REQ-005: Separate source correctness, installed parity, registration/trust, and fresh-runtime behavior.

#### Context Evidence

`install-skills.sh` provides idempotent skill and opt-in hook synchronization,
registration merging, provenance, backups, and restart guidance. The
project-spec observer and global-context references distinguish source tests
from runtime activation.

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
- TDD-003: A fresh runtime executes the advisory project-spec observer without
  creating spec state, denying tools, continuing Stop, or gating completion.

#### Validation Plan

Run changed-scope review, lint and syntax, security, regression, installer
parity, and fresh-session advisory hook checks in that order.

#### Test Plan

Use disposable source tests before local installation; after restart, run one
clean SessionStart, tool, and Stop sequence and separately verify bounded
advice plus neutral progression.

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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-004 -->

<!-- FEATURE: FEAT-005 reqs=REQ-006 status=superseded delivery=unassessed priority=P0 version=3 -->
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

A later internal-knowledge lookup exposed a narrower false block. Codex passed
canonical Atlassian Rovo tool names whose provider-prefixed operation segments
did not match the generic read-only verb grammar, so exact Confluence reads and
the connector's authentication bootstrap were denied as targetless ambiguous
effects before the connector ran. The denial proved local lifecycle
classification only; it did not establish whether Atlassian authorization was
present or absent.

#### Design Details

Replace the universal read-command allowlist with effect-oriented detection.
Explicit file writers, redirects, known mutating Git operations, mutating
tool methods, and declared write tools remain material. Common shell
composition is decomposed or conservatively scanned for those effects rather
than rejected merely for using a pipe or separator. Commands without a known
material effect pass through the project lifecycle and remain subject to their
normal execution sandbox, permissions, and product-specific hooks.

Classify the reviewed Atlassian Rovo read surface before the generic MCP method
grammar. Use exact full-name membership with the canonical hook prefix
`mcp__codex_apps__atlassian_rovo__` for these operation suffixes:
`atlassianuserinfo`, `fetch`, `getaccessibleatlassianresources`,
`getconfluencecommentchildren`, `getconfluencepage`,
`getconfluencepagedescendants`, `getconfluencepagefootercomments`,
`getconfluencepageinlinecomments`, `getconfluencespaces`,
`getissuelinktypes`, `getjiraissue`, `getjiraissueremoteissuelinks`,
`getjiraissuetypemetawithfields`, `getjiraprojectissuetypesmetadata`,
`getpagesinconfluencespace`, `gettransitionsforjiraissue`,
`getvisiblejiraprojects`, `lookupjiraaccountid`, `search`,
`searchconfluenceusingcql`, and `searchjiraissuesusingjql`. Both PreToolUse and
PostToolUse consume the same classifier, so these exact names remain
lifecycle-neutral for successful, failed, or conservatively shaped responses.

The provider branch is fail-closed. Reserve a case-normalized Rovo family stem
before generic parsing, while requiring case-sensitive exact membership for a
neutral result; known mutators, newly added methods, spelling, case, or version
drift, malformed separators, and the callable single-underscore alias remain
potential writes. Reserve Browser, Chrome, Playwright, Computer Use, and
arbitrary-JavaScript provider namespaces the same way so read-like method names
cannot inherit the generic exception. Other MCP providers retain the existing
generic verb grammar. An allowlisted connector may independently open the
default browser, refresh credentials, or store connector-owned authentication
state; those effects belong to connector settings, workspace policy, and
runtime trust rather than the selected-project write epoch.

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
- TDD-008: Every exact reviewed Atlassian Rovo read name is nonmaterial in all
  lifecycle phases, and both successful and failed PostToolUse payloads leave
  lifecycle bytes unchanged.
- TDD-009: Every current Rovo mutator plus unknown, renamed, case-drifted,
  malformed, spoofed, cross-provider, browser-control, and arbitrary-code names
  remains material and denied before execution when the phase requires it.
- TDD-010: A successful unknown or mutating Rovo PostToolUse response retains
  conservative material accounting if pre-use enforcement was absent or
  bypassed; an explicit failure remains epoch-neutral.
- TDD-011: Generic read-only MCP classification for other providers remains
  unchanged.

#### Validation Plan

Run focused and full source lifecycle-hook tests, project-spec contract tests,
Python syntax and changed-scope lint checks, skill-structure validation, and
changed-scope security and code review. Keep installed parity, restart, hook
trust, fresh-session behavior, and live connector access outside this tranche.

#### Test Plan

Use disposable Git roots and Codex homes to cover command composition,
executable ownership, known writers, Git subcommands, MCP/tool method
classification, private path identity, multi-target patches, symlinks, phase
transitions, and unchanged write epochs.

#### Evaluation Plan

Replay the exact blocked Rovo tool names in disposable lifecycle fixtures across
every phase, and independently verify that writes, provider drift, browser
control, implementation edits before `open`, and authoritative private-state
edits remain rejected. Do not invoke the live connector for source evaluation.

#### Rollout And Rollback

For the Atlassian Rovo classification tranche, validate source only. Do not
install or synchronize the hook, restart Codex, authenticate the connector, or
perform a live internal-document read. Installed parity, hook trust, fresh
session behavior, and live connector access remain separate follow-up gates.
Rollback reverts only the source classifier, tests, and aligned documentation.

#### Done Definition

The quote-aware shared analyzer, exact intent-to-add and private mode
transitions, external effect pass-through, protected control plane, lifecycle
Pre/Post symmetry, and focused hook regressions are implemented in source.
Agents can investigate and prepare required private inputs without lifecycle
deadlocks, while project mutations and authoritative lifecycle transitions
remain fail-closed and receipt-bound. Installation and fresh-runtime activation
remain separate rollout gates under REQ-005.
The exact reviewed Atlassian Rovo read set is additionally lifecycle-neutral by
canonical full name, while provider drift, mutations, browser control, and
arbitrary code remain fail-closed.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-005 -->

<!-- FEATURE: FEAT-006 reqs=REQ-007 status=ready delivery=unassessed priority=P0 version=1 -->
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
- TDD-002: The repo-owned guard false-block regression proves the unchanged
  config write passes after source repair while owner-private state remains
  protected and the project-spec observer remains neutral.

#### Validation Plan

Run config-codex contract tests, advisory project-spec hook tests, installer
checks, source-to-installed parity, and a fresh-runtime review boundary.

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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-006 -->

<!-- FEATURE: FEAT-007 reqs=REQ-008 status=ready delivery=unassessed priority=P0 version=7 -->
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

A later session exposed a separate remediation-profile divergence. A valid
`resolved` marker caused a new bare `$troubleshoot` invocation to mint pending
next-tranche authorization before any second remediation or independent
blocker existed. PreToolUse then denied discovery, and its repair text did not
state the parser's exact operation boundary: update an existing file, add only
an absent file, and never delete or delete/add-replace it.

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

Treat `resolved` as completed evidence rather than an exhausted terminal lock.
A later bare invocation returns the saved profile context and admits discovery
without pending authorization or marker replacement. If explicit profile flags
are supplied, reuse the profile-only resize handshake and preserve the resolved
blocker, tranche, attempt ledger, counters, lifecycle, and timestamps. Keep the
existing post-exhaustion next-tranche flow unchanged. While either handshake is
actually pending, admit only one exact `apply_patch` targeting the advertised
`current.md`: require `*** Update File` when it exists and permit `*** Add File`
only when it is absent. Deny `*** Delete File`, delete/add replacement, shell
rewrites, and every other tool with actionable feedback.

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
- TDD-007: A bare invocation from valid resolved state returns profile context,
  creates no pending or terminal authorization, and admits inspection. Explicit
  profile flags preserve resolved marker state through a profile-only handshake,
  while pending admission requires update for an existing task-state file, add
  only for an absent file, and rejects delete, delete/add, or shell alternatives.

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
second assistant report is created and peer workflow cleanup still occurs.

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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-007 -->

<!-- FEATURE: FEAT-008 reqs=REQ-009 status=ready delivery=unassessed priority=P0 version=4 -->
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

In the observed direct `$troubleshoot` session, no Task Implementer command was
invoked, but the submit hook found one compatible active Task Implementer
objective in its registry and silently bound the fresh session. That
auto-attachment staged metadata, injected a Task Implementer adapter
instruction, and caused an unrelated adapter validation attempt.

#### Design Details

Keep the separately owned internal `prompt-session-intake` skill, but make its
`UserPromptSubmit` process an unconditional pass-through boundary. After an
exact Task Implementer or SDLC init/run invocation binds the current session,
later direct turns in that same session may acquire only a bounded
session-state lock and
atomically stage an event-v2 metadata receipt with causal session/turn,
project, workflow, submitted digest, versioned operation identity, and an
unguessable acceptance token. It never writes the prompt body or `raw.md`.
An unbound direct turn returns no intake context before capture-specific
content checks. In an explicitly bound session, recognized secrets create no
event. Binding, writer, ambiguity, unsafe-state, or unexpected failures return
bounded capture-skipped context or no context with `continue: true`; they never
stop the user turn. The hook never semantically edits a prompt or invokes a
workflow.

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
identity. A fresh session without its own exact binding remains capture-inert
even when the registry contains exactly one compatible active objective. The
registry retains objective identity and bounded writer-session provenance for
diagnostics and validation, but it neither creates a new session binding nor
rejects a current turn because a different or stale writer value exists.

For Task Implementer, resolve logical project identity through the validated
workspace manifest. Treat `primary_root + scope` and the manifest-proven lane
`source_root` as aliases of one objective for explicitly bound capture,
writer-provenance updates, objective replacement, and result validation.
Those aliases never bind a fresh session. Continue to reject a project that is
not one of those two canonical identities.

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
Automatically attaching an unbound fresh session to a unique active objective
is rejected because project-level objective identity is not evidence that the
user selected that workflow in the new session; the explicit init/run path is
the single canonical binding transition.

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
- TDD-007: An unbound fresh session receives no intake context, binding, or
  event, including for secret-matching input and while one compatible Task or
  SDLC objective is active. After an exact binding, primary and lane Task
  project identities update one logical registry objective, while unrelated
  projects remain capture-inert.
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
manual edit, and ref-based explicit rerun. Before binding, leave one compatible
objective active and verify that a direct fresh-session prompt receives no
prompt-intake context and creates no binding or event. After binding, verify
every direct prompt reaches the agent, only the project-intent projection
reaches the canonical prompt once, and run counts change only after explicit
`run`.

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
managed lane reaches the current agent and, only after an exact binding in that
session, is captured in the same canonical objective prompt when safe and
material. An otherwise unbound fresh session remains capture-inert.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-008 -->

<!-- FEATURE: FEAT-009 reqs=REQ-010 status=ready delivery=unassessed priority=P0 version=3 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-009 -->

<!-- FEATURE: FEAT-010 reqs=REQ-011 status=ready delivery=unassessed priority=P0 version=4 -->
### FEAT-010: Claim-bound whole-repository commit transaction

#### Requirements Covered

- REQ-011: Commit complete multi-project repository changes through one safe transaction.

#### Context Evidence

The `commit` contract requires repository-root `git add -A`, staged review,
`git write-tree`, normal hooks, and final tree verification. Earlier project
lifecycle enforcement denied that documented path before its transaction owner
could run; the project-spec hook is now advisory. Worktree already uses durable
preparation claims and direct-child/tree verification; Task Implementer
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
bounded digests, explicit authorization source, status, and a
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

Project lifecycle state is advisory and is not part of commit admission. The
transaction accepts only one uncomposed canonical installed helper whose
interpreter, path, digest, current session, repository, worktree,
authorization, claim, and action flags match. Protect commit private state as
control-plane data. Existing repository instructions, Worktree ownership, and
active workflow commit policy remain independent gates.

For delegated Task Implementer commits, trust the hook runtime or one exact
executable named `python3` or `python3.N` that resolves to the same file by
basename through the hook process PATH. This admits a canonical worker Python
version different from the hook runtime while rejecting arbitrary same-name
paths and wrappers. `task-start` and base-state `task-recover` return a
transient structured commit context containing that resolved executable, the
canonical helper, exact scope cwd, worker root, raw `CODEX_THREAD_ID`,
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
tree, hook output, one-shot consumption, or recovery. Requiring any affected
project to emit a lifecycle attestation adds user ceremony without improving
the transaction's Git proof. Letting
Task Implementer auto-commit dirty source state broadens its explicit
integration authority. Allowing ordinary commit inside active Agentic SDLC
invalidates coordinator evidence and exact-head guarantees.

#### Implementation Boundaries

Owned surfaces are the `commit` helper, prompt hook, contract and real-Git
tests; transaction-private authorization and protected state; shared Worktree
ref coordination and delegated adapter; Task Implementer dirty-source handoff;
Agentic SDLC helper classification; corresponding README, installer
registration, canonical specs, and changelog. Lane integration,
feature sealing/promotion, publishing, and runtime installation remain with
their existing owners.

#### Test-First Success Criteria

- TDD-001: A direct `$commit` or a bounded leading directive such as
  `run $commit`, `apply $commit`, or `execute $commit` prepares and creates one
  exact commit from root files plus changes in multiple sibling project folders
  without project-spec lifecycle state.
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
the neutral project-spec hook, Worktree, Task Implementer interoperability,
SDLC policy and managed-outer integration, installer registration, security review, lint,
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-010 -->

<!-- FEATURE: FEAT-011 reqs=REQ-012 status=ready delivery=unassessed priority=P0 version=1 -->
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
Checkpointing active-generation or source-checkout dirt would blur lane
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-011 -->

<!-- FEATURE: FEAT-012 reqs=REQ-013 status=ready delivery=unassessed priority=P1 version=1 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-012 -->

<!-- FEATURE: FEAT-013 reqs=REQ-014 status=superseded delivery=unassessed priority=P0 version=4 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-013 -->

<!-- FEATURE: FEAT-014 reqs=REQ-015 status=ready delivery=unassessed priority=P0 version=1 -->
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

Land Task Implementer, Worktree, tests, specs, docs, and
changelog together. Install the cooperating skills and hook from one reviewed
source revision, verify parity, restart, and use a disposable probe before any
retained real run. Roll back the exact installed payload if activation fails;
do not prune or alter retained run resources.

#### Done Definition

A stopped retained run can restore only its exactly proven active-wave
checkouts and continue through normal fresh-worker recovery without pretending
that lost filesystem-only edits, a commit, or a promotion survived.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-014 -->

<!-- FEATURE: FEAT-015 reqs=REQ-016 status=ready delivery=unassessed priority=P0 version=1 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-015 -->

<!-- FEATURE: FEAT-016 reqs=REQ-017 status=ready delivery=unassessed priority=P0 version=1 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-016 -->

<!-- FEATURE: FEAT-017 reqs=REQ-018 status=ready delivery=unassessed priority=P0 version=3 -->
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
lease; and Git refs, registrations, worktree identity, heads, and index state.
Project lifecycle evidence is not an input. The planner returns exactly one of `execute`,
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
canonical requirements/design identity or non-managed-byte refresh after the
Task owner has updated the compiled requirements binding, while a stale
refinement or material impact still fails before plan mutation. Construct the new plan basis from the
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

Project-spec status is advisory during resume. Never simulate hook delivery,
wait for lifecycle continuation, or turn a recommended instruction change into
an automatic mutation. Already-effective repository instructions remain in
force; a separate explicit instruction workflow may be invoked independently.

Consume a dependent promotion-review correction graph one ready frontier per
integration cycle. The retained wave appends only tasks whose dependencies are
already indexed and merged, dispatches them from the latest integration head,
and leaves later frontiers unindexed until the next promotion-review boundary.
A future planned task may defer, then adopt, a dependency-only change to the
new correction chain; every other field and its resource-free plane remain
unchanged. Final fast-forward still requires the ordinary clean-lane and exact
integration-head proof.

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
ref, worktree, and commit truth. Maintain Project Specs retains canonical spec
semantics and advisory validation. Workers retain product changes and
direct-child commits. No public Task Implementer action or flag changes.

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
  republishes and binds a canonical-spec-only `retain_plan` impact, rejects a
  stale refinement or changed recovery impact, keeps the owner command out of
  ordinary help, and reaches ordinary resume adoption plus its controlled wave
  preparation afterward.
- TDD-014: A dependent correction chain is consumed one ready frontier at a
  time from successive integrated heads, updates a future planned task only by
  adding dependencies on the completed correction chain, then converges to one
  clean fast-forward.
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
activation, and workflow-owned finalization. Assert stable run/revision/plan/task
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-017 -->

<!-- FEATURE: FEAT-018 reqs=REQ-019 status=ready delivery=unassessed priority=P0 version=4 -->
### FEAT-018: PydanticAI-first portable agent application architecture

#### Requirements Covered

- REQ-019: Select the least-agentic sufficient architecture and use a PydanticAI-first portable runtime without surrendering application-owned controls.

#### Context Evidence

The repository separates whole-product design, agent-subsystem design, and AI
component selection across `design`, `ai-agent-design`, and `ai-stack`.
`ai-stack` applies typed boundaries, hard gates, replaceability, and
conditional infrastructure after it receives a frozen workload contract. Its
existing vendor mapping, however, treats Pydantic AI mainly as the non-OpenAI
agent option and maps OpenAI-native agents directly to a provider runtime. That
fragments the default application model and understates Pydantic AI's current
direct model, provider profile, capability, Agent Spec, Harness, durable
execution, instrumentation, MCP, and evaluation surfaces.

Current official Pydantic AI documentation describes model-agnostic access
over provider SDKs, a direct model request API, typed agents and dependencies,
model profiles, capabilities, MCP, fallback and concurrency primitives,
declarative Agent Specs, optional Harness specialists, durable workflow
integrations, OpenTelemetry instrumentation, and Pydantic Evals. Current
OpenAI Agents SDK and Anthropic managed-agent documentation describe
higher-level provider harnesses, supporting their classification as
specialized runtimes rather than ordinary model adapters.

#### Design Details

Consume the capability-level classification frozen by `ai-agent-design`:
deterministic code, direct calls, deterministic workflows containing model
calls, and agents remain separate choices. For a direct stack-only request,
`ai-stack` may perform the minimum provisional least-agentic check needed to
avoid selecting an agent runtime unnecessarily, but routes any disputed
behavior, topology, policy, or contract decision to `ai-agent-design`. During
an active handoff it never re-enters that skill. A direct call describes
one-request control flow, not a prohibition on the Pydantic AI `Agent`
primitive. For portable Python direct calls, use the low-level direct model API
when the
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
preserving the behavior and policy contract frozen by `ai-agent-design` and
application ownership above it. Add a focused provider-agnostic runtime
reference, then align the skill core, architecture, provider, baseline,
evaluation, metadata, README, source map, root catalog, project specs, and
changelog.

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
references; the adjacent `design` and `ai-agent-design` routing contracts; the
root README; canonical specs; and the `[Unreleased]` changelog. No provider,
service, infrastructure, credential, or production mutation is part of this
source change.

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
- TDD-010: Whole-product work follows `design` to `ai-agent-design` to
  `ai-stack` exactly once; direct stack-only work marks any local
  classification provisional and routes disputed behavior or policy decisions
  to `ai-agent-design` without recursive delegation.

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

The skill selects technology for the behavior and policy contract frozen by
`ai-agent-design`, uses Pydantic AI as its portable Python default, preserves
differentiated provider features, keeps application controls and state outside
provider runtimes, justifies every specialist or native escape, and
distinguishes source readiness from installed or fresh-runtime behavior.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-018 -->

<!-- FEATURE: FEAT-019 reqs=REQ-020 status=ready delivery=unassessed priority=P0 version=2 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-019 -->

<!-- FEATURE: FEAT-020 reqs=REQ-021 status=ready delivery=unassessed priority=P0 version=2 -->
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

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-020 -->

<!-- FEATURE: FEAT-021 reqs=REQ-022 status=ready delivery=unassessed priority=P0 version=1 -->
### FEAT-021: Semantic Git-effect classification for commit-push

#### Requirements Covered

- REQ-022: Keep bounded commit-push Git effects workflow-owned.

#### Context Evidence

The former project lifecycle classifier treated publication Git commands as
ambiguous and blocked the documented workflow before its owner could validate
them. Project lifecycle is now advisory. The existing commit transaction owns
reviewed local staging and commit effects; `commit-push` itself owns a narrow
semantic publication contract rather than relying on a project-spec hook or a
second raw-Git path.

#### Design Details

Parse Git tokens once after supported global options. Recognize only exact
read-only query grammars required by `commit-push`: `--version`, remote URL
lookup for `origin`, remote and local symbolic-ref reads, remote-ref listing,
show-ref verification, rev-list, and the existing read allowlist. Reject
unknown or write-capable options rather than generalizing entire subcommands.

Keep the bounded Git-effect classifier inside the publication workflow. Resolve
the repository's literal current branch from
`refs/heads/BRANCH` and require the command's source and destination to encode
that same branch without shell expansion, wildcard, additional refspec, or URL
target. Resolve every applicable `origin` URL with `get-url --all`, require
exactly one HTTPS, SSH, or scp-style network target, and validate its authority
strictly enough to reject local, file, external-helper, option-like, whitespace,
and multiple-target forms. The only admitted fetch is `origin` plus
`refs/heads/BRANCH:refs/remotes/origin/BRANCH` with flags that suppress
`FETCH_HEAD`, tag following, automatic maintenance, and commit-graph writes.
Its object and matching remote-tracking-ref updates are admitted by the
publication owner only when no executable reference-transaction hook can add
unmodeled worktree effects.

The only admitted publication is a non-force `git push` to `origin` from
`HEAD` to that literal branch, optionally with upstream setup. Before executing
it, resolve `core.hooksPath` and the repository's effective
`pre-push` and `reference-transaction` paths. Any active hook or inspection
ambiguity keeps the command denied because it can introduce unmodeled project
effects. Upstream setup is a known local Git-config effect, not a project-file
effect, and remains bounded to the already validated branch and remote.

Teach the existing commit prompt-intent owner that a bounded leading
`$commit-push` invocation also authorizes its local commit phase. Update the
workflow to call the claim-bound commit helper whenever the checkout is dirty;
raw staging and commit remain transaction-denied. Keep repository-owned
publication policies and remote authentication authoritative after workflow
classification.

#### Selected Option

Keep exact semantic Git-effect recognition at the `commit-push` boundary and
reuse the existing claim-bound commit transaction for local changes. Project
lifecycle remains neutral; cwd is not a proxy for Git safety, and no dual
commit path is created.

#### Alternatives Considered

Allowing generic Git commands in an external cwd was rejected because `-C`,
repository discovery, and Git metadata effects ignore that boundary. Treating
all fetch and push commands as external was rejected because force, delete,
mirror, tags, arbitrary refspecs, config-capable options, and custom hooks have
material effects beyond this workflow. A second commit-push-owned staging
helper was rejected because the established claim-bound transaction already
owns exactly that local effect.

#### Implementation Boundaries

Owned surfaces are commit-push Git parsing and classification, focused workflow
tests, commit prompt intent, `commit-push` guidance and trigger coverage, root
documentation, canonical specs, and changelog. Remote policy,
credentials, hosting-provider behavior, runtime installation, and an actual
push are outside this source repair.

#### Test-First Success Criteria

- TDD-001: Every exact read-only preflight query is non-material while
  write-capable or malformed variants remain material and ambiguous.
- TDD-002: Only the constrained literal current-branch fetch and push classify
  as external; detached head, branch mismatch, unsafe flags, multiple refspecs,
  alternate remotes, unsafe or multiple URLs, wildcard, and composition fail
  closed.
- TDD-003: Absent relevant hooks permit the exact remote shapes; an effective
  repository or configured pre-push or reference-transaction hook and
  hook-resolution failure deny them.
- TDD-004: A leading explicit `$commit-push` authorizes the existing commit
  transaction, while conversational mentions and raw staging/commit do not.
- TDD-005: Source regressions use disposable repositories and no remote, leave
  the developer checkout unchanged, and do not claim installed or live proof.

#### Validation Plan

Run focused commit-push and commit-intent tests first, then a neutral
project-spec hook regression, disposable Git-effect tests, scoped Ruff and
Python compilation, Markdownlint, source catalog and
project-spec validation, security review, code review, explicit changed-surface
alignment, and `git diff --check`.

#### Test Plan

Use a table-driven publication-workflow matrix for exact and adversarial Git
shapes, including `-C`, long/short option variants, config and worktree hook paths,
detached heads, unsafe or multiple URLs, unsafe refspecs, and composed shell
commands. Extend commit intent cases for direct and leading-imperative
`commit-push` invocations.

#### Evaluation Plan

Run the original blocked commands against the source hook in a disposable
repository and verify expected allow/deny classifications. Treat source
correctness, installed byte parity, registration/trust, restart, fresh-session
behavior, and real remote publication as independent gates.

#### Rollout And Rollback

Land source and tests first. Install the source and hook bundle only under
separate authorization, verify exact parity and trust, restart, then perform a
fresh-session preflight. Roll back the exact installed bundle if activation
regresses; never repair a publication failure by weakening unsafe command
denials.

#### Done Definition

The exact commit-push preflight and current-branch publication path are owned
by the publication workflow while project lifecycle remains neutral. Unsafe or
hidden-effect Git remains fail-closed, dirty local commits retain the
established transaction owner, and validation claims distinguish source repair
from installed and live behavior.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-021 -->

<!-- FEATURE: FEAT-022 reqs=REQ-023 status=ready delivery=unassessed priority=P1 version=3 -->
### FEAT-022: Provider-neutral AI agent design workflow

#### Requirements Covered

- REQ-023: Design governed production AI agent subsystems.

#### Context Evidence

The repository already separates whole-solution design, non-AI stack
selection, AI component selection, focused research, and architecture review
across `design`, `app-stack`, `ai-stack`, `research`, and
`system-design-rules`. The missing surface is a focused workflow that applies
production agent patterns and policies, freezes a workload contract for
`ai-stack`, and synthesizes the returned component decision into one governed
agent-subsystem architecture.

Current primary guidance converges on starting with the simplest sufficient
control flow, including a terminal deterministic-code result when no model is
needed; keeping consequential controls outside model discretion; defining
typed tool and state boundaries; evaluating trajectories as well as outcomes;
and treating durability, retries, idempotency, observability, and release
governance as software-system responsibilities.

The supplied production-agent engineering guide confirms those foundations
and exposes targeted gaps in the current skill: the trusted control plane,
model-directed execution plane, and durable session record are not yet a
first-class reference architecture; the bounded loop and session recovery
contract are implicit; and context compaction, memory curation, credential
mediation, sandbox isolation, independent outcome verification, and operator
control need more explicit design obligations.

#### Design Details

Create one implicitly invokable, stateless `ai-agent-design` skill. Whole-
product work follows `design` to `ai-agent-design`; it continues to
`ai-stack` exactly once only when a model-backed capability remains. A
deterministic-only classification takes precedence over an initial request for
AI-stack review and terminates with the explicit no-AI decision.
Its core workflow freezes outcome and acceptance gates; classifies each
capability as deterministic code, a direct model call, a deterministic AI
workflow, or an agent; selects the smallest sufficient single- or multi-agent
pattern; separates trusted control and policy, model-directed execution, and
durable session state behind versioned provider-neutral interfaces when an
agent is accepted; rejects those layers for simpler classes when they have no
current owner or requirement; defines the bounded agent loop, operator
controls, contracts, authority, context, state, failure, independent outcome
verification, evaluation, and operations policies; invokes `ai-stack` with the
fixed workload and constraints when AI component selection is needed, or
records that no AI component is required for deterministic-only scope; and
returns a complete logical subsystem architecture without performing
implementation.

Keep the always-loaded entrypoint focused on routing, workflow, guardrails,
validation, and output. Put detailed guidance in six topic references:
classification and patterns; runtime architecture and sessions; contracts,
context, and memory; tools, authority, and security; durability, failures, and
effects; and evaluation, observability, and governance. Keep technology
choices dated and replaceable by routing volatile selection to `ai-stack` and
`research` rather than freezing one provider framework in this skill.

The runtime architecture reference owns logical component responsibilities,
provider-neutral interfaces, the minimum session envelope and event taxonomy,
effect-aware checkpoints, leases, the bounded observe/decide/authorize/
persist-intent/act/record-receipt/verify loop, and user or operator progress,
steer, cancel, approval, and
escalation controls. For effect-capable actions it persists an authorized
immutable intent and idempotency identity before dispatch, then records the
receipt and independent verification so resume reconciles rather than blindly
replays a crash between dispatch and receipt persistence. Existing references
retain detailed ownership of context
and memory governance; action authority, credential mediation, sandbox and
supply-chain boundaries; effect recovery; and verifier, rollout, and incident
policy. A model completion claim is never completion proof: deterministic
environmental or programmatic verification takes precedence, semantic graders
are independent and bounded, and no grader may waive policy or safety gates.

When required, the `ai-stack` handoff contains the outcome, behavior class,
acceptance gates,
failure impact, data and tenant constraints, autonomy ceiling, tool inventory,
durability needs, quality, latency, availability, cost, ownership, fixed
decisions, and blocking unknowns. The returning component table retains each
component's status, evidence, requirement, owner, acceptance gate, and revisit
trigger. `ai-agent-design` integrates that table with control flow, contracts,
security, recovery, evaluation, and rollout decisions.

#### Selected Option

Use a provider-neutral agent-design coordinator with explicit adjacent-skill
handoffs and progressive disclosure. Keep it advisory and implicitly
invokable because matching work is read-only and does not authorize material
effects.

#### Alternatives Considered

Embedding the full methodology in `SKILL.md` was rejected because it would
make every invocation context-heavy. Duplicating `ai-stack` technology
selection was rejected because it would create two drifting authorities.
Making the skill own whole-product architecture was rejected because
`design` and `app-stack` already own those layers. A provider-specific skill
was rejected because the reusable policies and patterns do not require one
vendor.

#### Implementation Boundaries

Owned surfaces are the new skill entrypoint, metadata, README, six focused
references, trigger/process/quality evaluations, adjacent `design` and
`ai-stack` routing contracts, root catalog, changelog, and canonical specs.
Shared validators, installed copies, runtime configuration, provider accounts,
applications, and infrastructure remain unchanged by the source
implementation.

#### Test-First Success Criteria

- TDD-001: Trigger cases select the skill for agent-subsystem design and reject
  adjacent stack-only, whole-product, research-only, review-only, and
  implementation requests.
- TDD-002: A fixed known sequence remains a deterministic workflow, while
  model-owned tool choice or continuation is classified as agentic.
- TDD-003: A fixed support sequence remains a deterministic AI workflow with
  typed tools and approval before sending; it introduces one bounded agent only
  if model-owned action or continuation is a proven workload requirement.
- TDD-004: A long-running decomposable case uses explicit orchestrator-worker
  contracts, durable state, deduplication, partial-failure recovery, bounded
  authority, and layered evaluation.
- TDD-005: Every design with a model-backed capability passes a complete
  workload contract to `ai-stack` and incorporates its scoped component
  decision without inventing vendor facts. A deterministic-only design records
  `Skipped - no AI component required` and does not invoke AI technology
  selection.
- TDD-006: Whole-product work delegates from `design` to `ai-agent-design` and,
  when AI remains in scope, then to `ai-stack` exactly once; the frozen
  behavior, topology, policy, and contract decision is not reopened by the
  component-selection handoff. A
  disputed direct `ai-stack` classification delegates to `ai-agent-design`,
  receives only the frozen contract back, and completes component selection
  once without a nested stack handoff.
- TDD-007: Approval bindings carry authenticated approver identity,
  authorization evidence, decision time, separation-of-duties status, exact
  action arguments and preview, expiry, policy version, and one-use replay
  protection.
- TDD-008: Every accepted agent design separates trusted control and policy,
  model-directed execution, and durable session state; defines versioned model,
  session, tool, sandbox, memory, and trace interfaces; and leaves product
  selection to `ai-stack` capability negotiation.
- TDD-009: Long-running designs use an explicit session state machine,
  event/effect record, lease, budgets, effect-aware checkpoint, reconcile-
  before-retry behavior, and visible progress, steer, cancel, approval,
  escalation, notification, and cleanup controls.
- TDD-010: Completion is independently verified through deterministic checks
  before bounded semantic grading or human review. Revision loops cannot
  repeat consequential effects, waive hard gates, or continue after
  contradictory or non-actionable feedback.
- TDD-011: Governed memory uses candidate validation, provenance, scope,
  sensitivity, conflict/quarantine, versioning, promotion, correction,
  deletion, retrieval-impact evaluation, and rollback; offline curation is not
  represented as model self-improvement.
- TDD-012: Credential non-disclosure never substitutes for action
  authorization. Delegated credentials are short-lived and destination-bound,
  sandboxes are isolated with constrained mounts, resources, and egress, and
  outputs cross a validated artifact publication boundary.
- TDD-013: A fixed rules-based capability remains deterministic code and skips
  AI component selection. A direct model call remains one application-level
  operation when bounded transport or schema-repair retries occur without
  model-owned continuation, tools, or side effects.
- TDD-014: Every effect-capable dispatch persists its authorized immutable
  intent, exact action digest, and idempotency identity first. A crash after
  dispatch but before receipt persistence resumes through authoritative
  reconciliation, never an unqualified replay.
- TDD-015: A quality case entered from an active `ai-stack` workflow returns
  only the frozen disputed contract to the caller, performs no nested stack
  handoff, and leaves component selection to execute exactly once in the
  originating workflow.

#### Validation Plan

Run portable quick validation and the repository's strict target validator;
validate eval JSON; lint changed Markdown; run source-catalog and project-spec
validation; perform target-scoped code and security review; run changed-surface
alignment; and finish with `git diff --check`.

#### Test Plan

Use at least eight positive and eight negative trigger prompts, process cases
for classification and routing, and eight output-quality cases covering the
direct-call lower bound, a deterministic-code lower bound, a deterministic
approval-aware workflow, one bounded diagnostic agent, a durable
orchestrator-worker design, controlled effect recovery, and governed memory
curation, plus an originating `ai-stack` non-recursive handoff. Assert runtime
separation, contracts, authority, operator control, failure handling,
independent verification, evaluation, and rollout. Do not add validator
self-tests because shared validator logic is not changing.

#### Evaluation Plan

Compare the eight output-quality cases with the captured previous working-byte
baseline when a clean
runner is available. Treat trigger CSV validation as static evidence only;
installation, source-to-install parity, fresh-session routing, and evaluated
output quality remain separate gates.

#### Rollout And Rollback

Land and validate source first. Install only through the repository installer,
verify source-to-install parity, and use a fresh session for routing checks.
Rollback removes the new skill and its catalog/spec/changelog records together;
no compatibility shim or deprecated alias is retained.

#### Done Definition

The source skill is lean, public-safe, strictly validated, correctly routed
against adjacent skills, grounded in current production-agent practices, and
able to return a complete agent-subsystem architecture with explicit control,
execution, durable session, context, memory, effect, verification, and
operator-control contracts; an explicit `ai-stack` decision when an AI
component remains or a documented deterministic-only skip; and no implied
implementation authority.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-022 -->

<!-- FEATURE: FEAT-023 reqs=REQ-003,REQ-024 status=ready delivery=unassessed priority=P0 version=2 -->
### FEAT-023: Advisory historical lifecycle observer

#### Requirements Covered

- REQ-003: Bind explicit helpers without restoring lifecycle authority.
- REQ-024: Remove historical lifecycle-phase authority from ordinary work, Task Implementer, and Agentic SDLC.

#### Context Evidence

The enforcing lifecycle existed in three layers: the global project hook,
workflow-local historical phase receipts, and terminal completion gates. The
implementation now removes those authority edges while retaining canonical
spec validation and publication, bounded advisory observation, workflow-owned
controls, and the separate explicit project-instruction mutation service.

#### Design Details

Replace the historical lifecycle state machine with best-effort document
observation and metadata-only prompt intake. SessionStart reads the actual v2
markers and is silent when they are current; UserPromptSubmit injects
root-agent classification guidance without storing raw prompts. Project-spec
PreToolUse, PostToolUse, and Stop registrations do not exist. Hook failure or
unsafe input skips intake without denying tools or completion, while shared
Stop arbiters retain independent troubleshooting and SDLC decisions.

Task Implementer removes hook-facing lifecycle authorization,
project-instruction readiness, and terminal lifecycle seals from its
authoritative state and transition predicates. Its root coordinator still
binds the canonical v2 project-spec receipt as project-truth evidence before
dispatch. Prompt revision, semantic spec inputs, locked task graph, claims,
worktree identity, test and review evidence, promotion compare-and-swap,
journals, and recovery remain authoritative.

The ordinary `commit` workflow also treats project lifecycle status as
advisory. Its explicit current-turn authorization, claim-bound transaction,
repository instructions, Git state, secret checks, Worktree ownership, and
active workflow commit policy remain the only admission boundaries.

Agentic SDLC removes project instructions from the mandatory phase graph but
uses the canonical v2 pair before spec-dependent planning. Requirements,
design, plan, TDD, evaluation, UAT, Git, authorization, retry, and recovery
remain SDLC-owned gates. The exact intent/spec receipt is assignment evidence,
not transition authority; future project instructions remain a separate
advisory outcome under the instructions already loaded.

Explicit project-instruction mutation remains separate from the observer. Its
helper keeps canonical path, ownership, digest, concurrency, and recovery
checks, but a refusal or `reload_required` result cannot terminate an enclosing
Task Implementer or SDLC run.

#### State And Migration

No authoritative Task Implementer or SDLC schema migration is required because
the removed lifecycle receipts and overlays were adjunct evidence rather than
workflow identity. Loaders no longer import or evaluate them. Historical
lifecycle-only files are ignored; no successor record, no dual execution path,
and no compatibility command is created. Invalid workflow-owned state still
follows the owning workflow's existing recovery contract.

Private lifecycle observation records remain owner-only and content-free.
Maintenance counts the protected current bundle in aggregate bytes without
making it deletion-eligible, treats valid sibling project subjects as
irrelevant, and disables cleanup when registry or aggregate authority is
incomplete.

Remove the retired lifecycle transition functions from the importable project
spec library as well as from the public CLI. Historical records remain passive
inputs to inspection, migration, and retention only. SessionStart resolves the
canonical source-sibling or disposable-installed ownership-retention helper,
validates its owner-controlled ancestry and complete imported source set,
snapshots their identity and digests, copies only those verified bytes into an
owner-only temporary execution tree, and runs the classifier there under an
isolated bounded interpreter. It validates the exact structured result and
falls back to `unsafe` on missing, spoofed, timed-out, malformed, failed, or
concurrently changed classification. This makes 30/90-day cleanup reachable
without granting the helper project or workflow authority or allowing an
unsnapshotted sibling module into import resolution.

An auto-steering disposition for future project instructions remains advisory
unless the current user explicitly invokes the separate mutation workflow.
Task Implementer removes its orphaned owner-receipt validator instead of
keeping a second blocked validation path. Its transient worker commit context
uses `scope_cwd` under a hard-cut v2 schema rather than retaining a lifecycle-
named field. Both stateful skill contracts expose
the repository-required inputs, writes, process, idempotency, failure, and
must-not headings without reintroducing lifecycle gates.

#### Selected Option

Use one advisory historical-lifecycle/document observer with no tool,
workflow-transition, or Stop authority. Keep the canonical v2 project-spec
pair authoritative for spec-dependent planning and dispatch, keep Task
Implementer and Agentic SDLC as the sole owners of execution safety state, and
keep project-instruction mutation as a separately invoked guarded operation.

#### Alternatives Considered

Fixing only lock recursion was rejected because workflow-local and terminal
gates would still stop sessions. Retaining selected historical lifecycle
safety gates was rejected by the requested fully advisory boundary. Making
canonical spec validity advisory was rejected because durable intent, design,
and delivery evidence must remain one authoritative project contract. Deleting
canonical specs and project-instruction helpers was rejected because their
diagnostics and explicit mutation service remain useful.

#### Implementation Boundaries

Owned changes are the lifecycle hooks and maintenance helpers, duplicated Stop
arbiters, Task Implementer spec/prompt/terminal adapters, ordinary commit
guidance and transient Task commit context, Agentic SDLC phase and prompt-impact
adapters, explicit project-instruction integration, tests, skill metadata,
references, README guidance, installer surfaces, and release notes. Unrelated
service work and workflow-owned safety state are excluded.

#### Test-First Success Criteria

- TDD-001: Every lifecycle phase and lifecycle evidence failure allows an
  ordinary project write and Stop completion.
- TDD-002: Task Implementer completes planning through promotion and cleanup
  without lifecycle receipts or a terminal seal, while stale prompt, missing
  evidence, ownership mismatch, dirty integration, and branch drift still
  fail.
- TDD-003: Agentic SDLC completes its golden path without project-instruction
  or spec-alignment phases, while invalid artifacts, missing evaluation or UAT,
  unauthorized Git, and exhausted repair budgets still fail.
- TDD-004: A composed Stop result ignores malformed lifecycle advice but
  preserves an independent SDLC or troubleshooting blocker.
- TDD-005: Active legacy workflow state remains readable with lifecycle-only
  adjunct fields ignored and without predecessor rewriting or a compatibility
  execution path.
- TDD-006: Project-instruction render, ownership, or reload failure is reported
  without stopping Task Implementer or SDLC.
- TDD-007: Current-bundle storage accounting and sibling-project bootstrap
  regressions remain covered.
- TDD-008: No production import or public command exposes lifecycle transition
  actions, and legacy transition tests are removed rather than converted to
  compatibility behavior.
- TDD-009: The actual SessionStart callback deletes an eligible ownership-safe
  historical bundle and protects candidates when the canonical classifier is
  missing, spoofed, malformed, failed, timed out, rooted under writable
  ancestry, replaced during classification, or returns partial registry proof.
  A sibling module named like a standard-library import is excluded from the
  isolated execution tree.
- TDD-010: SDLC project-instruction steering stays advisory without explicit
  user invocation, the orphaned Task validator is absent, and strict stateful
  skill-structure validation passes.
- TDD-011: Direct commit needs no lifecycle seal or waiver, while explicit
  authorization, whole-repository review, secrets, Git state, Worktree
  ownership, and active SDLC policy still block their unsafe cases. Task worker
  commit context contains `scope_cwd` and no lifecycle-named cwd field.

#### Validation Plan

Run focused deterministic tests for each changed hook and workflow adapter,
then the complete Task Implementer and Agentic SDLC verifiers. Validate every
changed skill with the stateful alignment profile where applicable, run
Markdown and Python checks, perform code and security review, and finish with
changed-scope alignment and `git diff --check`.

#### Test Plan

Cover lifecycle-missing and lifecycle-faulted ordinary, Task Implementer, and
SDLC paths; composed Stop arbitration; old-state migration; explicit
project-instruction failure; and the independent maintenance regressions. Pair
each permissive lifecycle case with a workflow-owned failure that must remain
denied.

#### Evaluation Plan

Compare deterministic source results with the captured working-byte baseline,
then run disposable installed and fresh-session workflow probes. Report static,
runtime, and output-quality evidence separately without inferring activation
from source parity.

#### Rollout And Rollback

Land source and migration tests first. Install into a disposable Codex home,
verify source parity, start a fresh session, and run disposable Task
Implementer and SDLC probes with injected lifecycle faults. A rollback restores
the prior source as one coherent version; do not reintroduce mixed advisory and
enforcing layers.

#### Done Definition

Historical project lifecycle state can be observed and reported but cannot
deny, continue Stop, or gate ordinary, Task Implementer, or SDLC completion.
Canonical spec validity remains required at spec-dependent planning and
dispatch boundaries, workflow-owned safety controls remain proven by paired
negative tests, and activation claims remain separated from source validation.

#### Implementation Evidence

No implementation evidence was recorded before schema v2 migration.

#### Verification Evidence

No independent verification evidence was recorded before schema v2 migration.

<!-- /FEATURE: FEAT-023 -->

<!-- FEATURE: FEAT-024 reqs=REQ-025 status=ready delivery=verified priority=P0 version=4 -->
### FEAT-024: Unified prompt-to-project-contract lifecycle

#### Requirements Covered

- REQ-025: Maintain one project contract across every agent workflow.

#### Context Evidence

The former implementation split semantic parsing between the canonical owner
and Task Implementer, bound prompt capture only after explicit workflow startup,
used schema v1 documents, and derived SessionStart/UserPromptSubmit advice from
an optional historical `lifecycle.json`. The installer merged current source
registrations but did not retire source-owned event registrations removed from a
new manifest.

#### Design Details

Use `maintain-project-specs` as the single semantic owner. Schema v2 keeps
requirement status, design readiness, and delivery evidence distinct. The owner
exposes byte-level document and pair inspection for worktrees and arbitrary Git
commits, envelope-preserving rendering, explicit v1 migration, and a locked,
failure-atomic pair publisher bound to Git HEAD and both prior byte digests.
The publisher retains verified no-follow project/docs directory descriptors,
performs every read/replace/restore relative to the held docs descriptor,
rechecks Git and both document states after each replacement, and restores only
bytes still equal to its candidate. Canonical inspection takes the matching
shared lock so owner-aware readers cannot observe an in-process split pair;
uncoordinated direct reads are not a transaction interface.

Register only SessionStart and UserPromptSubmit observers. UserPromptSubmit
stores an owner-only metadata receipt containing identity digests and injects a
bounded root-agent classification contract. It excludes secret-bearing and
non-root generated turns and serializes identical concurrent turn delivery.
SessionStart reads actual document markers through
bounded, no-follow, owner-controlled regular-file reads and stays silent when
v2 ownership is current. Neither event supplies tool or Stop authority.

Ordinary root agents classify and publish. Task Implementer and Agentic SDLC
are authoring adapters over the owner contract and reconstruct required syncing
from immutable workflow prompts when needed. Workers receive the root intent
identity and canonical spec receipt; they may return typed `spec_gap` proposals
but never write the documents or set verified delivery. Task and Agentic path
inventories disable Git rename folding so a protected source remains visible
after a move, and their typed gap summaries/evidence are sensitive-screened
before persistence or dispatcher output.

#### Selected Option

Use one v2 owner with two non-blocking intake/observation hooks and explicit
workflow adapters. Keep classification in the root agent because hooks have no
model-semantic authority, while deterministic owner code validates and publishes
the resulting canonical pair.

#### Alternatives Considered

Retaining lifecycle phases was rejected because unavailable observer state
created misleading status and risked implicit workflow authority. Letting every
workflow keep its parser was rejected because formats and completion semantics
diverged. Hook-side keyword classification was rejected because it cannot
reliably distinguish durable requirements from conversational requests.

#### Implementation Boundaries

Owned surfaces are the canonical contract/parser/renderer/migration/transaction,
project-spec hooks and manifest, hook installer retirement, Task Implementer and
Agentic SDLC adapters/templates/contracts, project instruction schema references,
focused tests, READMEs, the root project contract, and release notes. Unrelated
service projects and their dirty specification documents remain excluded.

#### Test-First Success Criteria

- TDD-001: Missing `lifecycle.json` is silent and cannot emit
  `ADVISORY_UNAVAILABLE`; missing or v1 canonical documents report their actual
  bounded status.
- TDD-002: Every eligible direct prompt receives root classification context and
  metadata-only intake, while secrets and generated worker/continuation turns
  create no prompt receipt; concurrent delivery of one turn remains idempotent.
- TDD-003: Normal parsing rejects all v1 formats and explicit migration produces
  one valid v2 rich-record pair with stable IDs and preserved envelopes.
- TDD-004: Paired publication rejects stale HEAD or document bytes, detects
  boundary-time Git/document changes, cannot follow a swapped `docs` path,
  restores exact unchanged originals after a later write failure, and blocks an
  owner-aware reader until the pair is complete.
- TDD-005: Task Implementer and Agentic SDLC accept the same v2 receipt and do
  not carry independent schema/semantic validation paths. Both reject a
  protected spec rename even when its destination is broadly claimed, and
  typed gaps containing sensitive text fail before persistence or output.
- TDD-006: Hook installation removes only exact obsolete source-owned
  PreToolUse/PostToolUse registrations and preserves unrelated hooks and the one
  shared Stop arbiter.

#### Validation Plan

Run Python compilation and lint for changed modules, focused owner/hook/workflow
and installer tests, skill contract checks, Markdown lint, changed-surface
alignment, and `git diff --check`. Then install into a disposable Codex home and
compare exact hook bytes and registrations. Treat a fresh Codex session as a
separate activation check.

#### Test Plan

Add deterministic unit tests for byte-level parsing, status separation,
migration, CAS publication, prompt metadata safety, direct/worker/generated
classification routing, actual-document SessionStart reporting, adapter
delegation, and targeted installer retirement. Preserve workflow-owned negative
controls for authorization, state, and evidence failures.

#### Evaluation Plan

Verify an ordinary direct implementation request, Task Implementer prompt, and
Agentic SDLC prompt all reach the same canonical contract semantics. Separately
probe a question/status prompt, secret-bearing prompt, and worker result to prove
they do not create lifecycle writes.

#### Rollout And Rollback

Migrate source documents and validate them before installing. Use the repository
installer for a disposable home, verify exact source/install parity, and restart
Codex for hook activation. Rollback restores the prior coherent source and
manifest; it does not reintroduce dual normal parsers or lifecycle phase state.

#### Done Definition

One v2 project-contract owner serves ordinary, Task Implementer, and Agentic SDLC
work; direct prompts receive best-effort root classification; paired documents
record requirements, design, implementation, and verification honestly; workers
cannot fork project truth; obsolete hook authority is absent; and focused source,
install, and activation evidence is reported independently.

#### Implementation Evidence

- `maintain-project-specs` now owns schema-v2 parsing, rendering, migration,
  byte-pair validation, and Git/document-digest-bound paired publication. Its
  transaction uses held directory descriptors, repeated boundary checks,
  guarded rollback, and a shared owner-reader lock.
- SessionStart observes the canonical documents with bounded no-follow
  regular-file reads and UserPromptSubmit stages serialized digest-only
  root-turn intake.
  The project-spec manifest has no tool or Stop event, and installer
  reconciliation retires only its obsolete exact registrations.
- Task Implementer and Agentic SDLC assignments bind the root intent and the
  accepted project-spec receipt. Worker results carry typed `spec_gaps` and
  cannot publish or independently reclassify project intent. Their path checks
  disable rename folding and their gap fields reject sensitive text.
- Canonical templates, workflow documentation, release notes, project
  instructions, and path-scoped CI use the same owner and schema-v2 contract.

#### Verification Evidence

- Canonical owner, paired transaction, and project-spec hook suites pass 24,
  10, and 38 cases respectively, including committed-exact disable policy,
  boundary races, reader serialization, and rejection of symlinked or oversized
  canonical documents.
- Task Implementer passes 65 wave, 13 worktree-interoperability, 13 spec, and
  49 resume cases plus its contract smoke check.
- Agentic SDLC passes 79 execution, 108 hook, 45 prompt-workspace, 9 canonical
  validation, 11 start-contract, and 12 worker-dispatch cases.
- Installer hook reconciliation passes 29 cases; project-instruction generation
  and retention pass 88 and 8 cases. Ruff, ShellCheck, actionlint, Markdown
  lint, skill-structure checks, and canonical pair inspection pass.
- Disposable installed-package parity and activation in a newly started Codex
  session were not executed and are not inferred from source verification. The
  current session may continue to emit its already-loaded older hook message.

<!-- /FEATURE: FEAT-024 -->
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
