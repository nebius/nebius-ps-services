---
name: sdlc-workflow-test
description: "Use only when explicitly asked, outside Agentic SDLC, to verify the workflow with the no-flag check or --create/--keep/--resume/--destroy for one owned local three-tier Docker app plus GUI UAT and sanitized cleanup."
---

# SDLC Workflow Test

## Help

For `$sdlc-workflow-test --help` or `$sdlc-workflow-test -h`, return concise help and stop before
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
- Do not use to install, sync, trust, or repair hooks unless the user asks for
  that separate remediation after reviewing the report.
- Do not use on production repositories or real customer projects.
- Do not use as a replacement for `sdlc-start` or any `sdlc-*` phase skill.

## Inputs

- Lifecycle flags: no lifecycle action, `--create`, `--create --keep`,
  `--resume`, or `--destroy`. Preserve every existing lightweight verifier option when no
  lifecycle action is present. `--keep` alone is invalid, and `--destroy` is
  mutually exclusive with create/keep; validate these rules before mutation.
- `docs/agentic-sdlc-design.md`.
- Optional user-specified dedicated verification root, report path under that
  private verification root, global skills path, or design path. A new custom
  root is initialized with an ownership marker; an existing custom root must
  already contain that valid marker.
- Existing required Agentic SDLC phase skills under `~/.agents/skills`.
- Existing Codex hook configuration under `~/.codex/hooks.json` or
  `~/.codex/config.toml`.
- Optional prior verification report and disposable state under
  `~/.codex/sdlc-verification/`.
- Optional private live-results manifest passed with `--live-evidence PATH`.
- Canonical retained three-tier semantic result passed with
  `--three-tier-results PATH` whenever the three-tier profile claims PASS.
  The default is `~/.codex/sdlc-verification/live-results.json`; its contract
  is `assets/live-results.schema.json`.

The default report path is:

```text
~/.codex/sdlc-verification/report.md
```

## Must Not

- Do not create a new SDLC CLI or make hooks orchestrate phases.
- Do not create project-local skills.
- Do not modify installed global skills under `~/.agents/skills`.
- Do not edit, delete, install, trust, or rewrite hooks under `~/.codex/hooks`
  or hook configuration.
- Do not run on a production or user project. Use only the disposable
  verification project.
- Do not push, create a real PR, merge, force-push, or publish anything.
- Do not commit private SDLC state, hook logs, screenshots, transcripts, local
  plans, or evidence.
- Do not treat conversation memory as authoritative workflow state.

## Required Reads

- `docs/agentic-sdlc-design.md`.
- `references/verification-checklist.md`.
- `assets/live-results.schema.json` when producing or ingesting live evidence.
- The `SKILL.md` files for all required Agentic SDLC phase skills being
  verified.
- The SDLC hook README, PreToolUse hook, Stop hook, and hook unit tests from
  the `sdlc-start` skill's hook bundle when hook verification is in scope.
- `scripts/verify_agentic_sdlc.py` before patching or relying on verifier
  behavior beyond its command-line help.
- For a three-tier mode only: `references/three-tier-live.md`,
  `assets/three-tier-prompt.md.template`,
  `assets/three-tier-results.schema.json`, and
  `scripts/three_tier_lifecycle.py` help for the needed private action. Read
  `scripts/three_tier_semantics.py` before changing PASS derivation.

## Writes

Allowed writes:

- `~/.codex/sdlc-verification/`.
- A disposable verification project under
  `~/.codex/sdlc-verification/disposable-project/`.
- Disposable local state for the verification project only.
- `~/.codex/sdlc-verification/report.md`.
- An explicitly selected report path only when it remains under the private
  verification root and has no symlinked component.
- `~/.codex/sdlc-verification/verification-context.json` and optional
  `live-results.json` plus referenced evidence artifacts, all private local
  files outside the disposable Git root.
- A private verification-root ownership marker and a committed public fixture
  marker that prevent custom roots or clean unknown Git repositories from
  being mistaken for verifier-owned state.
- For a three-tier mode only, one active private lifecycle under
  `<verification-root>/three-tier-live/`, its owned disposable project, raw
  evidence, exact Docker inventory, sanitized per-run report, and lifecycle
  archive as defined by `references/three-tier-live.md`.

Do not write to real project source trees, installed skill folders, hook
configuration, credential directories, external systems, or non-disposable Git
remotes.

## Process

1. Parse the invocation mode before any mutation.
   With no lifecycle flag, run steps 2-10 exactly as the existing lightweight
   verifier and do not create or inspect a Docker or browser application. With
   `--create` or `--create --keep`, first run the unchanged lightweight
   preflight, then safely destroy the previous active exactly owned test
   environment before preparing a fresh replacement and following the
   Three-Tier Live Process below. Never run two live test environments for one
   verification root.
   With `--resume`, skip project creation, revalidate the one retained failed
   or partial lifecycle, and continue from its earliest incomplete recorded
   phase. With `--destroy`, skip project creation and follow the standalone
   destroy process in `references/three-tier-live.md`. A missing active
   application is the successful idempotent result `ALREADY_DESTROYED`.
2. Establish the source of truth.
   Read `docs/agentic-sdlc-design.md` and
   `references/verification-checklist.md`. Treat the design doc as the
   workflow contract and the checklist as the test plan.
3. Run static and hook preflight verification.
   From the skills repository root, run:

   ```bash
   python3 sdlc-workflow-test/scripts/verify_agentic_sdlc.py
   ```

   This script checks source-installed parity for all required SDLC skills and
   the `maintain-project-specs`, `worktree`, `nebius-grafana-query`,
   `project-agent-instructions`, and conditional `troubleshoot` runtime
   support, explicit-only invocation policy, canonical shared spec ownership,
   prompt workspace/history/
   rename/lifecycle regressions, execution scope/recovery/replan/secret gates,
   sequential fallback, Task Implementer interoperability, the composed
   managed outer-worktree lease lifecycle, bounded observability contract,
   normalized failure/diagnosis/repair-control and corrective-plan contracts,
   exact-SHA PR handoff and canonical single-action merge modes, verifier
   self-tests, and hook fixtures. The disposable fixture is a nested selected
   folder in a local
   monorepo-shaped Git repository. A clean canonical flat fixture with the
   exact expected tracked tree and no remote is migrated once. Unknown,
   unowned, dirty, remote-backed, or non-canonical directories and repositories
   fail closed without mutation. The script does not edit installed skills or
   hooks.
4. Review the preflight report.
   Any deterministic FAIL makes the report FAIL. Missing optional hook
   registration is WARN/PARTIAL; a configured payload mismatch or unsafe hook
   behavior is FAIL. Missing live evidence is PARTIAL, not synthetic PASS.
5. Run the disposable golden-path workflow when full verification is requested.
   Use the disposable project only. Explicitly load and follow these phase
   skills in order:
   `sdlc-create-requirements`, `sdlc-start`, `sdlc-gather-context`,
   `sdlc-create-design`, `project-agent-instructions`, `sdlc-auto-steering`,
   `sdlc-create-plan`,
   `sdlc-prepare-execution`, `sdlc-tdd`,
   `sdlc-implement-plan`, `sdlc-validate-codes`, `sdlc-unit-tests`,
   `sdlc-evaluate`, `sdlc-update-documents`, `sdlc-align-specs`,
   `sdlc-commit`, and `sdlc-uat-tests`. Run `sdlc-update-documents` again
   after UAT when final docs changed. Do not use `sdlc-merge-pr`, and do not
   create a real PR.
6. Verify rerun and change-request behavior.
   Repeat `$sdlc-start run <prompt-ref-or-file>` with no prompt
   changes, then edit the same prompt with the safe change
   request from the checklist and confirm stable IDs, immutable locked plans,
   scoped changes, refreshed evidence, and no duplicate commits.
7. Verify failure routing and steering behavior.
   Inject one controlled failure at a time in the disposable project, verify
   a proven mechanical cause bypasses troubleshooting, an ambiguous cause
   enters `troubleshoot` exactly once and returns its diagnosis to
   `sdlc-classify-failure`, unresolved evidence stops, and a proven localized
   defect re-enters through a corrective plan and appended wave. Exercise a
   budget stop without resetting counters, then repair and rerun. Add the
   pause/no-PR instruction to the same prompt and repeat `run`.
8. Verify continuation and optional harness smoke checks.
   Exercise Stop continuation with fake state and, where available, run safe
   GUI and TUI smoke checks against local disposable targets only.
9. Persist and ingest live results.
   Use the identity in `verification-context.json`. Write only the v3 manifest
   and relative evidence paths defined by `assets/live-results.schema.json`.
   Collect raw evidence with `scripts/collect_live_evidence.py`; do not copy or
   hash assertion/profile artifacts ad hoc. Each evidence profile must name its
   exact allowed source schema and at least one collector-produced source
   artifact. Profile status and assertion booleans alone are never provenance.
   Structurally valid generic artifacts are PARTIAL only. PASS is fail-closed
   until both the assertion and its canonical profile source have dedicated
   machine-semantic validators; never promote a source by relabeling it.
   Deterministic and merge-safety profiles are verifier-derived rather than
   supplied in the live manifest: they become valid only when every mapped
   deterministic capability or the explicit merge-authorization guard passes.
   Lightweight PASS remains fail-closed until its collector derives exact
   claims from checkpoint, Git, test, steering, document, and repaired-failure
   bytes. Three-tier PASS requires the copied source to byte-match
   `--three-tier-results` and pass the existing strict layer, artifact, phase,
   Git, ordered-GUI, correlation, and restart-persistence validator.
   Evidence must stay under `evidence/<lane>/` within the verification root,
   use private permissions, match the exact preserved baseline/final Git
   identity, include a real selected-scope golden-path commit, keep every commit
   in the live history inside the selected nested project, exclude private SDLC
   state, and never contain prompt bodies or secrets. Rerun the verifier with
   `--live-evidence PATH`; include `--three-tier-results PATH` when that
   profile is present and claims PASS.
10. Update the report.
   Keep the report concise and evidence-backed. Include capability-level PASS,
   PARTIAL, or FAIL, validation commands, skipped live checks, and the low-risk
   repository recommendation. Do not paste raw evidence bodies, hook logs, or
   secret-bearing output.

## Three-Tier Live Process

Use this process only after explicit `--create`, `--create --keep`, or
`--resume`:

1. Read and follow `references/three-tier-live.md`. Confirm Docker Engine,
   Docker Compose, Git, installed-source skill parity, canonical Google Chrome,
   and the `computer-use` capability before live mutation. The lifecycle helper
   must launch Chrome directly with a fresh verifier-owned user-data directory,
   new process group, and verification-ID window marker; never use or close an
   existing Chrome instance. Prove Computer Use with a successful real
   `get_app_state` that exposes the exact marker before every action. Tool or
   process discovery alone is not proof. Missing required live capability
   before an attempt is PARTIAL. A failed attempted action is FAIL.
2. For create modes, prepare one owned lifecycle with
   `scripts/three_tier_lifecycle.py`. Its `prepare` action serializes lifecycle
   changes, destroys the previous active environment through the same exact
   ownership-checked cleanup path as standalone destroy, and only then creates
   a fresh verification ID and project. If ownership, project safety, or
   cleanup cannot be proven, stop without creating a replacement. Cleanup must
   canonicalize and deduplicate every recorded/discovered Docker alias by
   resource identity after validating both exact ownership labels, covering a
   prior run interrupted before inventory capture. Its cumulative ledger must
   survive failed retries, and an already-absent resource counts as success only
   when a fresh inspect proves absence. Retain the returned verification ID as
   this invocation's immutable
   generation fence. Pass it as `--expected-verification-id` to every later
   mutating helper action; never refresh it from a newer status response. Run
   the helper's `prepare-images` action to pull only the fixed
   public base images through an owned empty Docker CLI config; do not reuse
   that config for Compose. For resume mode, require the existing lifecycle to
   be owned, KEPT, and previously FAIL or PARTIAL; revalidate its project
   boundary and recorded resources without creating replacements. Use the
   private root's isolated Codex home for all prompt workspace and phase state;
   never reuse or delete the user's ordinary Agentic SDLC run directory.
3. Create the project through the normal prompt-bound Agentic SDLC workflow:
   first run `$sdlc-start workspace init <project-folder>`, then use
   `scripts/render_three_tier_prompt.py` to replace the generated starter body
   while preserving its managed identity, and finally run
   `$sdlc-start run <prompt-ref-or-file>`. Follow the returned phase
   skill; do not make the lifecycle helper or hooks orchestrate phases.
4. Build all three logical layers: browser GUI, Django/Gunicorn web/API server,
   and PostgreSQL. Run exactly two labelled Compose containers, dynamically
   publish only the web port on loopback, and keep PostgreSQL private to the
   Compose network. Run every Compose action through the helper's
   generation-locked `run-compose` action with the immutable expected
   verification ID; never invoke a mutating `docker compose` command directly.
   This makes a replacement wait for an in-flight action and prevents a
   superseded invocation from starting or changing a stack. Record containers
   with the role-specific `--web-container` and `--database-container`
   arguments; the helper verifies Compose service labels before accepting them.
5. Execute every phase and test class in the scenario reference. Local ship
   means build and run the promoted clean SHA locally; it never means push,
   publish, PR creation, or PR merge.
6. For GUI evaluation and UAT, explicitly route through `sdlc-gui-test` with
   `harness: computer-use`. Immediately before the first navigation in
   `sdlc-evaluate`, and again immediately before `sdlc-uat-tests`, require a
   fresh successful `get_app_state` for the exact selected browser. Unless the
   current Codex surface explicitly confirms locked Computer Use is enabled for
   this session, the host must be unlocked. A normal browser window must be
   visible, unminimized, foreground, and on the current macOS Space.
   Lock/unlock, display, Space, or browser-window changes invalidate earlier
   readiness. Refresh accessibility state after every successful action.
   Correlate GUI observations with
   independent API and PostgreSQL results and prove persistence across a
   service restart.
7. If a just-in-time capture returns `cgWindowNotFound` or another visibility
   failure, record `ENVIRONMENT_DEFECT` with the explicit stage
   `pre-navigation-window-capture` and state that no GUI navigation or action
   was attempted. Record only bounded sanitized diagnostics: selected browser,
   whether lock/window visibility/frontmost/current-Space state is known, and
   whether the call returned an error or timed out. If a Computer Use call
   hangs or times out, or fresh capture loses responses, treat
   the shared service as unhealthy and stop all further Computer Use calls for
   that attempt. Do not use the same path for `list_apps`, new-window recovery,
   repeated browser retries, browser restart, or service restart. Exact owned
   process cleanup remains available without Computer Use. Fresh-session or
   service recovery remains a separate explicitly authorized action.
8. Persist semantic results incrementally using
   `agentic-sdlc/three-tier-results-v2`. Failed runs retain validated partial
   layer/test status; the lifecycle helper derives PASS from
   required phases, tests, Git identity, GUI actions, API/database correlation,
   restart persistence, and distinct artifacts. Record canonical per-phase JSON
   results and the three structured Computer Use readiness stages. Never accept
   placeholder `{"result":"pass"}` evidence or screenshots as the only oracle.
9. Write the complete sanitized report with layer inventory, resolved ports
   and endpoints, baseline/promoted SHAs, phase/test/UAT outcomes, exact owned
   resource IDs, recorded validation commands, top issues and recommended
   fixes, and cleanup/retention result.
10. With `--keep`, preserve the owned project, private SDLC state/evidence, two
   running containers, network, database volume, built web image, and exact
   verifier-owned Chrome instance/profile; report `KEPT` and the later destroy
   invocation. Without `--keep`, revalidate and close only its recorded process
   group, then destroy every exact owned live resource in a finally-style path.
   Browser or Docker identity ambiguity persists resumable `CLEANUP_FAILED`
   state before ambiguous mutation. Any cleanup failure makes the result FAIL.

## Idempotency

- The verifier may be rerun at any time.
- Reuse the same verification root and overwrite only generated verification
  files under `~/.codex/sdlc-verification/`.
- Preserve or supersede prior reports by writing the current report atomically.
- Do not duplicate requirements, design, plans, tests, commits, or evidence in
  the disposable project when inputs are unchanged.
- If a previous verification run is incomplete, resume from the report and
  disposable state instead of deleting unrelated user files.
- The three-tier profile permits one active application per verification root.
  Every `--create`, including `--create --keep`, replaces the previous active
  environment: exact owned cleanup must finish before a fresh lifecycle can be
  created. Every later helper mutation and every Compose action is fenced by
  the immutable verification ID, so a superseded workflow stops before its
  next mutation instead of recreating the old stack. Cleanup ambiguity fails
  closed and blocks replacement. Standalone destroy is
  resumable and returns `ALREADY_DESTROYED` when no active lifecycle exists.
  Destroy retains sanitized reports and the lifecycle archive, but removes the
  owned project, raw evidence, private run state, containers, network, database
  volume, and built image. Recorded tab state remains sanitized audit metadata,
  while the browser tab itself is user-managed: standalone destroy and
  replacement cleanup never close it or gate cleanup on whether it remains
  open.
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
  Git fixture or canonical flat migration source, fail closed without chmod,
  file writes, or commits. Any disposable Git remote is also a failure.
- If hook configuration is malformed or an SDLC hook command does not target
  the canonical payload under `$CODEX_HOME/hooks`, report FAIL rather than
  treating registration as missing or comparing an unrelated canonical file.
- If the golden-path SDLC run fails, route through `sdlc-classify-failure` and
  record the earliest responsible phase in the report. Use `troubleshoot` only
  for the controlled ambiguous-failure lane; it remains absent from the golden
  sequence.
- If live evidence is absent, stale, dirty, symlinked, overly permissive,
  outside the verification root, or schema-invalid, never infer success.
- If a requested profile requires unavailable tooling such as Computer Use,
  mark it PARTIAL instead of granting synthetic success. `NOT APPLICABLE` is
  reserved for checks outside the requested profile; an exact 20-skill PASS
  requires both GUI and TUI evidence.
- If a just-in-time Computer Use capture fails before navigation, classify it
  as `ENVIRONMENT_DEFECT`, not a product, URL, evaluation, or UAT defect. If the
  call hangs or the shared service stops responding, make no further Computer
  Use calls in that attempt. Cleanup does not call Computer Use: it revalidates
  the exact verifier-owned Chrome PID, process group, executable, and profile.
  Any mismatch preserves the owned runtime as `CLEANUP_FAILED`.
- If any command would push, publish, merge, edit installed hooks, or touch a
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
with a disposable `CODEX_HOME`. Full workflow verification may create local
commits only inside the disposable project. It must never push, open real PRs,
merge, publish, alter credentials, or mutate installed hooks or skills.

The verification root must be a dedicated, verifier-owned directory outside
the source repository. It uses private `0700` permissions on POSIX; the root
marker, context, manifest, report, and referenced evidence files require
private modes. The disposable Git root must have no remote and must carry the
exact public fixture marker. The root, fixture components, and report path must
be real local directories and files, never symlink redirects.

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

- `~/.codex/sdlc-verification/report.md` exists.
- Static skill discovery and hook configuration checks are recorded.
- PreToolUse and Stop hook fixture results are recorded.
- Prompt workspace initialization, revision, steering, terminal, and legacy
  lifecycle results are recorded.
- The deterministic capability matrix records prompt, execution, fallback,
  outer lease, Task Implementer interop, steering, observability, hook, and
  verifier results, including failure-event/diagnosis/repair-control,
  corrective-plan, explicit-ref/head publication and explicit-PR, canonical
  single-action merge authorization.
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
- No installed skills, hook configuration, credentials, real repositories, or
  external services were modified.
- In three-tier create mode, every logical layer, SDLC phase, named test class,
  local deployment, computer-use GUI journey, API/database correlation, and
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

Return:

- Final readiness status: PASS, PARTIAL, or FAIL.
- Report path.
- Top issues found.
- Recommended fixes.
- Whether it is safe to try the SDLC workflow on a low-risk real repository.
- Validation commands run.
- Live or external tests skipped and why.
