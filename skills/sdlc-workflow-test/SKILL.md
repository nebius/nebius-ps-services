---
name: sdlc-workflow-test
description: "Use only when explicitly asked, outside Agentic SDLC, to verify the workflow with the no-flag check or --create-live-test/--keep/--resume/--destroy with deterministic checks, one owned local three-tier Docker app, GUI UAT and cleanup of both fixture types."
disable-model-invocation: true
---

# SDLC Workflow Test

## Help

For `$sdlc-workflow-test --help` or `$sdlc-workflow-test -h` (including native Claude forms), return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Agent Compatibility

Before stateful verification, read `../global-context-management/references/agent-hosts.md`.
The lifecycle binds the selected agent through its isolated native home (`codex-home/` or `claude-home/`);
set that host's native home variable for the public workflow. Resume and cleanup
require the recorded host. Never transfer a trial or infer live proof from fixtures.

Use `$sdlc-workflow-test` in Codex, `/sdlc-workflow-test` in Claude Code, or
`/skills:sdlc-workflow-test` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Verify whether the Agentic SDLC workflow works as designed without joining the
workflow as an SDLC phase. This skill is a test harness and review workflow for
the SDLC system itself.

## When To Use

- The user asks to test, verify, validate, audit, or smoke-check the Agentic
  SDLC workflow.
- The user wants to know whether required Agentic SDLC phase skills and
  optional SDLC hooks are installed and working as designed.
- The user wants a safe verification report before trying the SDLC workflow on
  a real repository.
- The SDLC design, hook source, state schema, or phase skills changed and need
  a disposable regression pass.

## When Not To Use

- Do not use as part of a normal Agentic SDLC product run.
- Do not use to implement product features directly.
- Do not repair ordinary installed skills or hooks during verification. Explicit
  maintenance uses their installer before the trial; live fixture setup may install
  reviewed runtime/hooks into only its owned disposable native home.
- Do not use on production repositories or real customer projects.
- Do not use as a replacement for `sdlc-start` or any `sdlc-*` phase skill.

## Inputs

- Lifecycle flags: no lifecycle action, `--create-live-test`, `--create-live-test --keep`,
  `--resume`, or `--destroy`. Preserve every existing lightweight verifier option when no
  lifecycle action is present. Retired `--create` and `--three-tier-results`
  are rejected without aliases or abbreviation. `--keep` alone is invalid, and `--destroy` is
  mutually exclusive with create/keep; validate these rules before mutation.
- `docs/agentic-sdlc-design.md`.
- Optional user-specified dedicated verification root, report path under that
  private verification root, global skills path, or design path. A new custom
  root is initialized with an ownership marker; an existing custom root must
  already contain that valid marker.
- Existing required Agentic SDLC phase skills under `~/.agents/skills`.
- Existing selected-host hook registration: Codex `hooks.json`/`config.toml`,
  or Claude `settings.json`, under `<agent-home>`.
- Optional prior verification report and disposable state under
  `<agent-home>/sdlc-verification/`.
- Optional private live-results manifest passed with `--live-evidence PATH`.
  The default is `<agent-home>/sdlc-verification/live-results.json`; its contract
  is `assets/live-results.schema.json`.

The default report path is:

```text
<agent-home>/sdlc-verification/report.md
```

## Must Not

- Do not create a new SDLC CLI or make hooks orchestrate phases.
- Do not create project-local skills.
- Do not modify either host's installed skill catalog.
- Do not modify ordinary native hooks or their configuration. Disposable native
  setup precedes the trial and uses the existing installer and normal native trust
  review; never manufacture trust records or bypass the host review control.
- Do not run on a production or user project. Use only the disposable
  verification project.
- Do not push, create a real PR, merge, force-push, or publish anything.
- Do not commit private SDLC state, hook logs, screenshots, transcripts, local
  plans, or evidence.
- Do not treat conversation memory as authoritative workflow state.

## Required Reads

- `../worktree/references/workflow-invariants.md` for shared ownership and evidence.
- `references/lightweight-runtime-verification.md` for full lightweight runtime verification.
- `references/live-runtime-ownership.md` before any three-tier live operation.

- `../worktree/references/document-delivery.md` for source coverage, phase/slice
  identities, shared-skill caller contracts and protected completion policy.

- `docs/agentic-sdlc-design.md`.
- `references/verification-checklist.md`.
- `assets/live-results.schema.json` when producing or ingesting live evidence.
- The `SKILL.md` files for all required Agentic SDLC phase skills being
  verified.
- The SDLC hook README, PreToolUse hook, Stop hook, and hook unit tests from
  the `sdlc-start` skill's hook bundle when hook verification is in scope.
- `scripts/verify_agentic_sdlc.py` before patching or relying on verifier
  behavior beyond its command-line help.
- For a three-tier mode only: `references/three-tier-process.md`,
  `references/three-tier-live.md`,
  `assets/three-tier-objective.md.template`,
  `assets/three-tier-results.schema.json`, and
  `scripts/three_tier_lifecycle.py` help for the needed private action. Read
  `scripts/three_tier_semantics.py` before changing PASS derivation.
- For phased three-tier qualification, read `references/phased-live-qualification.md`.

## Writes

Allowed writes:

- `<agent-home>/sdlc-verification/`.
- A disposable verification project under
  `<agent-home>/sdlc-verification/disposable-project/`.
- Disposable local state for the verification project only.
- `<agent-home>/sdlc-verification/report.md`.
- An explicitly selected report path only when it remains under the private
  verification root and has no symlinked component.
- `<agent-home>/sdlc-verification/verification-context.json` and optional
  `live-results.json` plus referenced evidence artifacts, all private local
  files outside the disposable Git root.
- A private verification-root ownership marker and a committed public fixture
  marker that prevent custom roots or clean unknown Git repositories from
  being mistaken for verifier-owned state.
- For a three-tier mode only, one active private lifecycle under
  `<verification-root>/three-tier-live/`, its owned disposable project, raw
  evidence, exact Docker inventory, sanitized per-run report, and lifecycle
  archive as defined by `references/three-tier-live.md`.

Do not write to real project source trees, ordinary installed skill folders,
ordinary hook configuration or credentials, external systems, or non-disposable
Git remotes. The separately declared disposable native setup stores only its
necessary authentication in that home's protected native store.

## Process

1. Parse the invocation mode before any mutation.
   A direct natural-language request to implement the disposable three-tier
   test application selects create mode; do not require the user to repeat a
   flag. Retention still requires an explicit keep request.
   With no lifecycle flag, run steps 2-6, including the conditional full-runtime
   reference in step 5, as the existing lightweight
   verifier and do not create or inspect a Docker or browser application. With
   `--create-live-test` or `--create-live-test --keep`, first run aggregate owned
   cleanup through `verification_workspace.py --verification-root <root> destroy`,
   then run fresh lightweight preflight before preparing the live replacement
   and following the Three-Tier Live Process below. Never run two live test environments for one
   verification root.
   With `--resume`, skip project creation, revalidate the one retained failed
   or partial lifecycle, and continue from its earliest incomplete recorded
   phase. With `--destroy`, skip project creation and follow the standalone
   aggregate destroy process in `references/three-tier-live.md`. Return
   `ALREADY_DESTROYED` only when both live and lightweight fixtures are absent;
   missing ownership or orphaned raw state is cleanup failure.
2. Establish the source of truth.
   Read `docs/agentic-sdlc-design.md` and
   `references/verification-checklist.md`. Treat the design doc as the
   workflow contract and the checklist as the test plan.
3. Run static and hook preflight verification.
   From the skills repository root, run:

   ```bash
   python3 sdlc-workflow-test/scripts/verify_agentic_sdlc.py
   ```

   This script checks source-installed parity, explicit-only invocation,
   canonical spec ownership, advisory project-lifecycle independence, direct
   objective/binding regressions, execution and secret gates, Implement and
   managed-worktree interoperability, bounded observability, repair control,
   exact-SHA publication modes, verifier self-tests, and hook fixtures. Its
   required capability matrix includes complete document-package coverage,
   phase/slice ordering, current completion evidence, large task graphs and
   bounded retained worktrees. Shared semantic and delivery owners participate
   in installed parity. Missing required tests fail; skipped tests stay partial.
   Supplied-phase and unphased-source fixtures exercise reviewed planning,
   multiple slices, source-plan drift, missing future coverage and contradictory
   dependencies. They validate admission, not autonomous model planning quality.
   Native phased qualification requires the separate live process and its
   owner-derived program evidence. Deterministic fixtures alone do not qualify
   native planning or execution. A local trial never qualifies a remote merge.
   The disposable fixture is a nested selected folder in a local
   monorepo-shaped Git repository. Obsolete flat fixtures and unknown,
   unowned, dirty, externally connected, or non-canonical directories and repositories
   fail closed without mutation. The script does not edit installed skills or
   hooks.
4. Review the preflight report.
   Any deterministic FAIL makes the report FAIL. Missing optional hook
   registration is WARN/PARTIAL; a configured payload mismatch or unsafe hook
   behavior is FAIL. Missing live evidence is PARTIAL, not synthetic PASS.
   Static or fixture prerequisite failure stops before regression suites launch.
   A source/design change preserves the existing fixture context; use aggregate
   owned cleanup before fresh preflight. If the context was already replaced,
   follow the hash-proven recovery in
   [owned context restoration](references/three-tier-live.md#owned-context-restoration).
5. For full lightweight verification, read and follow
   [lightweight runtime verification](references/lightweight-runtime-verification.md).
   Preserve its order: golden path, rerun/change request, controlled failure and
   steering, continuation, then live evidence ingestion. Missing live evidence
   remains PARTIAL; deterministic preflight alone is not runtime proof.
6. Update the report.
   Keep the report concise and evidence-backed. Include capability-level PASS,
   PARTIAL, or FAIL, validation commands, skipped live checks, and the low-risk
   repository recommendation. Do not paste raw evidence bodies, hook logs, or
   secret-bearing output.

## Concurrent project architecture

The live fixture is one monorepo and one native home. Run the complete SDLC app
in project-a concurrently with a public Implement companion in project-b,
following `references/three-tier-process.md`. Keep the original app, phase,
native, GUI and cleanup oracles. Record inner promotion and final aggregate
separately; require both objective delivery proofs and fresh aggregate runtime
acceptance. Multiple exact launcher registrations share one cleanup owner.

## Three-Tier Live Process

Before startup, phase collection or runtime targeting, read
[live runtime ownership](references/live-runtime-ownership.md).

For each phase observation, write its private `phase-result-v6` JSON at
`<run-root>/evidence/phases/<phase>.json` and pass that exact file to
`record-phase --evidence`. Read the installed helper's subcommand help before
invocation. The recorder owns immutable attempt archives; callers never write
them. `record-validation` is for canonical acceptance checks; recorded failures
are immutable. Follow `references/three-tier-live.md` for development evidence.

For explicit `--create-live-test`, `--create-live-test --keep`, or `--resume`, read and follow
`references/three-tier-process.md` before any live operation. It owns all ten
steps, including capability proof, immutable generation fencing, normal SDLC
execution, GUI readiness, evidence collection and exact-resource cleanup.
The mode, authority, failure and completion rules in this file remain required.
Run native lifecycle, launcher and workspace commands in fresh processes from
the selected host's installed catalog. Preparation pins that physical catalog
and its runtime closure; source and installed paths are not interchangeable even
when bytes match. The launcher rejects a source-catalog native launch. Source
preflight stays separate, and aggregate ingestion delegates read-only semantic
validation to the configured installed owner. Never normalize an execution basis
or import a second catalog into an existing owner process.

## Idempotency

- The verifier may be rerun at any time.
- Reuse the same verification root and overwrite only generated verification
  files under `<agent-home>/sdlc-verification/`.
- Preserve prior report bytes in the private reports archive before a new run;
  publish incremental and final reports atomically.
- Do not duplicate requirements, design, plans, tests, commits, or evidence in
  the disposable project when inputs are unchanged.
- If a previous verification run is incomplete, resume from the report and
  disposable state instead of deleting unrelated user files.
- The three-tier profile permits one active application per verification root.
  Every `--create-live-test`, including `--create-live-test --keep`, replaces the previous active
  environment: exact owned cleanup must finish before a fresh lifecycle can be
  created. Every later helper mutation and every Compose action is fenced by
  the immutable verification ID, so a superseded workflow stops before its
  next mutation instead of recreating the old stack. Cleanup ambiguity fails
  closed and blocks replacement. Standalone destroy is
  resumable and returns `ALREADY_DESTROYED` only when both fixture classes are absent.
  Destroy retains sanitized reports and the lifecycle archive, but removes the
  owned live and lightweight projects, origins, profiles, auxiliary fixture homes,
  context-bound raw evidence, containers, network, database volume and built image. Every terminal path closes and verifies the absence
  of its exactly owned browser processes. Ambiguous browser ownership blocks
  cleanup rather than targeting personal browser sessions.
- `--resume` accepts only an owned KEPT run whose prior result is FAIL or
  PARTIAL, revalidates its project boundary, and continues from recorded state.
  It never creates a second application or infers prior PASS evidence.

## Failure Handling

- If required global skills, `worktree`, or installed parity are missing, write
  FAIL. Missing optional hook registration is PARTIAL; configured source/
  installed hook drift is FAIL.
- If hook fixture tests fail, keep all fixture state under the verification
  root and report the failing checks without mutating installed hooks.
- If a deterministic subprocess times out or cannot start, record a concise
  FAIL result and still write the report.
- If execution-plane scheduler or real-Git lifecycle tests fail, stop before
  the golden path; do not attempt worker dispatch or promotion.
- If the disposable project is dirty from a prior verification run, inspect and
  reuse or supersede only verification-owned files; do not delete unknown files.
- If the verification root, disposable project, canonical fixture paths,
  installed skill roots, configured hook payloads, or requested report path
  contain symlinks, fail closed before following or mutating them.
- If an existing custom verification root lacks its ownership marker, or an
  existing disposable directory is non-empty and not an exact marked verifier
  Git fixture, fail closed without chmod,
  file writes, or commits. Reject every remote except the exactly owned local
  bare `origin` validated by the fixture contract.
- If hook configuration is malformed or an SDLC hook command does not target
  the canonical payload under `<agent-home>/hooks`, report FAIL rather than
  treating registration as missing or comparing an unrelated canonical file.
- If the golden-path SDLC run fails, route through `sdlc-classify-failure` and
  record the earliest responsible phase in the report. Use `troubleshoot` only
  for the controlled ambiguous-failure lane; it remains absent from the golden
  sequence.
- If live evidence is absent, stale, dirty, symlinked, overly permissive,
  outside the verification root, or schema-invalid, never infer success.
- If a requested profile requires unavailable tooling such as Chrome or Playwright Test,
  mark it PARTIAL instead of granting synthetic success. `NOT APPLICABLE` is
  reserved for checks outside the requested profile; an exact 20-skill PASS
  requires both GUI and TUI evidence.
- Required web acceptance uses headless Playwright Test, fresh owned processes
  and contexts, and digest-bound stage receipts. No native desktop capture or
  unlocked-screen prerequisite applies. Preserve failed stages and clean up
  exact owned processes; identity ambiguity remains `CLEANUP_FAILED`.
- If any command would push, publish, merge, edit ordinary installed hooks, or touch a
  non-disposable project, stop and report the unsafe action.
- For three-tier cleanup, inspect every recorded/discovered alias, require both
  ownership labels, resolve its canonical Docker identity, and deduplicate by
  kind plus identity before deletion. Preserve cumulative removed,
  already-absent, and remaining ledgers across retries; infer absence only from
  a fresh successful inspect result. Any mismatched label, browser identity,
  path, schema, or verification ID fails closed before ambiguous mutation.
  Never discover deletion targets from
  name prefixes.

## Safety

The helper script is limited to static inspection, local fixture setup, local
Git operations inside the disposable verification project, and hook execution
with a disposable selected agent home. Full workflow verification may create local
commits only inside the disposable project. It must never push, open real PRs,
merge, publish, alter ordinary credentials, or mutate ordinary installed hooks or skills.

The verification root must be a dedicated, verifier-owned directory outside
the source repository. It uses private `0700` permissions on POSIX; the root
marker, context, manifest, report, and referenced evidence files require
private modes. The disposable Git root must carry the exact public fixture
marker and have exactly one verifier-owned local bare `origin`. Its private
receipt binds the project, owner generation, frozen baseline and default branch.
Creation, evidence recording, resume and cleanup share one validator. Reject
external or extra remotes, URL overrides, unsafe paths, borrowed objects,
repository-local executable configuration, conditional includes and changed
origin refs or push guards. Every fresh fixture has a private Git configuration
profile sealed to its generation and origin receipt. The shared validator
excludes inherited configuration and global/system attributes; only its exact
owned environment overlay is accepted. Startup, workers, commit admission,
evidence, resume and cleanup revalidate it without ambient fallback. Launch
native commands through `scripts/run_with_git_environment.py` with the exact
owned profile and checkout. It supplies environment only, never SDLC transitions.
The native launcher removes unrelated inherited variables and parent identities,
requires the exact private native home, and prevents user shell initialization
from restoring credentials. Configure the disposable host using the
[native launch settings](references/three-tier-process.md#native-launch-settings).
Do not dump environment values or use this launcher for application runtime
secrets; those belong to the outer lifecycle's protected runtime input.
A missing or altered profile requires a failed report, not silent recreation.
The root, fixture components, and report path must be real local directories
and files, never symlink redirects.

The three-tier lifecycle helper is additionally limited to private lifecycle
state, sanitized reports, exact label verification, and deletion of recorded
owned resources. It does not invoke SDLC phases. It must never delete Docker,
shared/base images, unrelated containers, networks, volumes, projects, browser
applications, or unrelated tabs. Database credentials stay private and must
not appear in reports, committed prompts, screenshots, or command-line
arguments.

When executing hook source fixtures, disable Python bytecode writes so
verification does not create `__pycache__` artifacts in skill source folders.

If the active environment appears to be a real production repository, stop and
ask the user to confirm a disposable verification path.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Completion Criteria

- Three-tier PASS requires before/during/after statistics bound to the same
  immutable initial baseline and owner history. Read-only canonical result
  admission rechecks that evidence; missing or altered reports cannot retain
  PASS. Cleanup preserves sanitized reports outside the disposable native home.

- Three-tier test PASS uses owner-executed Django result receipts and existing
  Playwright UAT receipts; read `references/test-evidence.md` before collecting
  test evidence. Free-form logs and declared counts cannot certify tests.

- `<agent-home>/sdlc-verification/report.md` exists.
- Static skill discovery and hook configuration checks are recorded.
- PreToolUse and Stop hook fixture results are recorded.
- Direct objective intake, revision, steering, terminal, and obsolete-state rejection
  lifecycle results are recorded.
- The deterministic capability matrix records objective, execution, fallback,
  outer lease, Implement interop, steering, observability, hook, and
  verifier results, including failure-event/diagnosis/repair-control,
  corrective-plan, explicit-ref/head publication, owner-produced qualification,
  protected admission, review drift and ambiguous merge recovery.
- The private v3 live manifest records digest-backed semantic assertions for
  all seven live lanes and an explicit evidence row for every required Agentic
  SDLC skill. Profile results also bind exact source schemas to collector-made
  source artifacts.
- Full verification records the golden-path, idempotency, change-request,
  failure-routing, auto-steering, documentation-update, and
  steering-continuation lanes through validated private evidence; missing lanes
  remain PARTIAL.
- Any required deterministic or supplied live FAIL makes final status FAIL;
  PASS requires all required deterministic and live lanes to pass.
- The report states PASS, PARTIAL, or FAIL and lists top issues and fixes.
- No ordinary installed skills, hook configuration, credentials, real repositories,
  or external services were modified by verification; disposable setup is reported separately.
- In three-tier create mode, every logical layer, SDLC phase, named test class,
  local deployment, headless Playwright Test GUI journey, API/database correlation, and
  restart-persistence assertion has semantic evidence. The report records all
  ports/endpoints and exact owned resources. The final lifecycle is either
  safely `KEPT` by explicit request, `DESTROYED`, or retained as
  `CLEANUP_FAILED`; incomplete cleanup is FAIL.

## Output Contract

Return the requested mode, final PASS/PARTIAL/FAIL result, report path, checked
environment, project and promoted SHA when applicable, layer inventory,
resolved web/API/health endpoints, internal-only database endpoint, phase/test/
UAT summary, retention or cleanup status, and any exact next action. Never
return raw logs, prompts, credentials, database contents, private endpoint
secrets, or screenshot contents.

For the lightweight no-flag mode, preserve the existing readiness report and
PASS/PARTIAL/FAIL interpretation.

Include the top issues and recommended fixes, validation commands, and skipped
live or external checks with their reasons. State whether the evidence supports
trying the workflow on a low-risk real repository.

## Verifier Ownership And Progress

`verification_suites.py` owns suite commands and capability dependencies; the
shared `workflow_verification.py` owns exact test inventories and process scheduling.
At most two explicitly audited independent groups overlap; other groups remain
exclusive. Healthy suites have observation checkpoints with no duration expiry.
Any required failure stops queued work and cancels owned peers with cleanup grace.
The parent reports exact completed, interrupted and not-run cases and checks frozen
catalog identity before admitting native work. Follow the shared
health and timing contract in `../worktree/references/execution-health.md`.

`verification_workspace.py` owns root admission, exact lightweight generation
receipts, operation exclusion and aggregate cleanup. It delegates live resources
to the three-tier owner. Preserve sanitized reports and minimal cleanup records.
Missing profiles or generation receipts require a fresh root, not adoption or
manual deletion. Stop owned native coordinators normally and let the product
release workers, validators and leases before cleanup; absence of a browser alone
is not writer quiescence. Historical receipts are never converted into authority.

## Task acceptance coverage

The three-tier fixture seeds an independent domain harness before its Git
baseline. `prepare-images` builds one owned nonroot dependency image and freezes
`task-acceptance-profile.json` in the private root. The native SDLC coordinator
uses it for early task container checks, records a deferred filtering criterion,
and resolves all debt on promoted and aggregate trees. Final observation derives
coverage from owner receipts; handwritten summaries cannot satisfy this lane.
Existing promoted test classes, fresh browser stages, restart persistence,
aggregate rebuild and Implement companion remain required.
