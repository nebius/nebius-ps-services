# Agentic SDLC Verification Checklist

Use this checklist as the durable test plan for `sdlc-workflow-test`.

## Required SDLC Skills

- `align`
- `sdlc-auto-steering`
- `sdlc-classify-failure`
- `sdlc-commit`
- `sdlc-create-design`
- `sdlc-create-plan`
- `sdlc-create-requirements`
- `sdlc-evaluate`
- `sdlc-gather-context`
- `sdlc-gui-test`
- `sdlc-implement-plan`
- `sdlc-merge-pr`
- `sdlc-prepare-execution`
- `sdlc-start`
- `sdlc-tdd`
- `sdlc-tui-test`
- `sdlc-update-documents`
- `sdlc-uat-tests`
- `sdlc-unit-tests`
- `sdlc-validate-codes`

## Report Sections

The report at `~/.codex/sdlc-verification/report.md` must include:

- Summary
- Environment checked
- Capability regression results
- Skill discovery results
- Hook configuration results
- PreToolUse safety test results
- Stop continuation test results
- Disposable SDLC golden-path run results
- Idempotency results
- Failure-loop results
- Steering behavior results
- Live workflow results
- Validation commands
- Skipped live or external checks
- Low-risk real repository recommendation
- Gaps found
- Recommended fixes
- Final readiness status: PASS, PARTIAL, or FAIL

## Design Contract

Verify `docs/agentic-sdlc-design.md` includes:

- canonical `maintain-project-specs` semantic, schema, template, validation,
  and receipt ownership; routed `sdlc-create-requirements` and
  `sdlc-create-design` authoring adapters; and no legacy phase-owner metadata
- core workflow terms: no workflow CLI, `sdlc-start`, PreToolUse, Stop, private
  local run state, and resume/idempotency
- `Workflow Verification`
- `Quick preflight test`
- `Full workflow test`
- `Real three-tier application test` and lifecycle `--resume`
- `$sdlc-workflow-test`
- `$sdlc-start`
- `$sdlc-start workspace init [project-folder]`
- `$sdlc-start run <prompt-ref-or-file>`
- `agentic-sdlc/prompt-v3`, Ask-only required input, immutable raw and intent
  revisions, requirements refinement, same-prompt steering, durable FIFO
  queueing, linked completed follow-ups, `ALREADY_COMPLETE`, and fail-closed
  `WORKFLOW_UPGRADE_REQUIRED`; the private refinement verifier must bind the
  latest accepted intent to the exact compiled requirements file before design
- schema-v7 execution, exact initialized-folder scope, `task-arm`, direct
  `task-heartbeat`, read-only `task-watch`, confirmed-stopped `task-requeue`,
  `task-recover`, task-finish crash adoption, `replan-future`, process-group
  monitored sequential `codex exec` fallback, and `worktree-interop-v2`
  coordinator state over v4 Worktree leases
- `allow_implicit_invocation: false`
- `~/.codex/sdlc-verification/report.md`
- `sdlc-auto-steering`
- `sdlc-update-documents`
- `steering/auto-steering.json`
- `documents.md`
- steering dispositions such as `requirements-change`, `design-change`, and
  `docs-update`
- path-agnostic filesystem target handling and ordinary outbound network
  command allowance, with only unsafe content or guarded action checks
- bounded observability evaluation with a predefined operational criterion,
  non-Grafana provenance, explicit attribution/coverage, one grade-changing
  query per provider call, and pass/fail/inconclusive outcomes
- publication-only `create-pr` and findings-and-readiness-only `review-pr`
  modes that preserve the clean exact promoted SHA
- exact-head `sdlc-merge-pr` authorization for one explicit
  `gh pr merge --match-head-commit` command with a specific PR target and no
  extra action or bypass flag

## Static Discovery

Verify:

- The real source catalog passes the shared skill-structure validator; fixture
  self-tests are not a substitute for catalog validation.
- Every required phase skill is free of legacy direct spec-owner or v2
  project-instruction wording.
- Both rich Agentic spec templates retain `maintain-project-specs` ownership,
  managed-region markers, and their narrow Markdownlint envelopes.
- The requirements adapter README states every main boundary as `Do not`.
- Global skill folders exist under `~/.agents/skills` for all required Agentic
  SDLC phase skills.
- Each skill has `SKILL.md`.
- Each `SKILL.md` has valid `name` and `description` front matter.
- Skill names match folder names.
- No duplicate SDLC skill names exist.
- Each SDLC description starts with
  `Use only as part of the Agentic SDLC workflow;`.
- Each required Agentic SDLC phase skill has `agents/openai.yaml` with
  `policy.allow_implicit_invocation: false`.
- No project-local `.agents/skills` directory is required by the disposable
  project.
- The installed `align`, `worktree`, `nebius-grafana-query`, and conditional
  `troubleshoot` support skills exist. Project lifecycle observations are
  advisory and are not runtime dependencies.
- Every required SDLC skill, all four runtime support skills, and
  `sdlc-workflow-test` match their source copies, excluding installer
  provenance and bytecode artifacts.

## Hook Configuration

Verify read-only:

- Missing optional hook registration is WARN/PARTIAL, not FAIL.
- Malformed hook JSON or TOML is FAIL, not missing optional registration.
- Codex-managed `[hooks.state]` TOML metadata is not treated as an inline hook
  event.
- When configured, PreToolUse and Stop hook entries point to the expected SDLC
  payloads under the canonical `$CODEX_HOME/hooks` install location. A basename
  match at another path is FAIL. Compare only validated canonical entrypoints
  plus their shared runtime libraries against the source hook bundle; hook test
  fixtures are not installed-runtime payloads.
- Existing non-SDLC `SessionStart` and `UserPromptSubmit` hooks are preserved.
- `UserPromptSubmit` does not perform SDLC routing.
- Stop continuation routes through the explicit prompt-bound `sdlc-start run`
  action.
- Active PR and merge authorization binds both the exact promoted head and the
  recorded symbolic remote-default branch and HEAD; later default drift fails
  closed.

Do not install, trust, edit, delete, or rewrite hooks during verification.

## PreToolUse Safety

Allow cases:

- Read-only Git commands: `git status`, `git diff`, `git log`.
- Normal source and test edits inside the disposable repo.
- General filesystem reads, writes, updates, deletes, and moves regardless of
  target path, including outside-repo files, credential directories, Codex
  runtime files, global `AGENTS.md`, locked SDLC plans, and private SDLC state.
- Ordinary outbound network commands such as `curl`, `ssh`, and `scp`.
- Local SDLC state writes in the disposable verification state.
- Read-only MCP-like operations where fixture support exists.
- Project validation commands.

Deny cases:

- Commit without valid `sdlc-commit` authorization.
- Commit on protected branches.
- Staged secrets.
- Push without PR authorization.
- `gh pr create` or a GitHub PR-creation MCP call without PR authorization.
- Force push.
- Merge or PR merge without merge authorization.
- PR merge whose command, PR target, local/promoted head, explicit user request,
  or `--match-head-commit` guard differs from its authorization.
- PR merge with implicit PR selection, admin bypass, branch deletion,
  repository override, another unsupported flag, a shell operator, or an
  appended command.
- Sensitive Git/GitHub action through an executable wrapper, absolute
  executable path, prepended command, nested shell, operator, or redirection.
- Broad destructive shell commands.
- Patches containing obvious secret material.

Authorization handoff:

- Commit, PR, and merge authorization files allow only the matching guarded
  action while valid.
- PR authorization binds `phase: "create-pr"`, the exact branch, current
  promoted HEAD, passing UAT status, and expiry. GitHub PR reads remain allowed
  without PR-creation authorization. Pushes allow exactly `origin` plus one
  `HEAD:<branch>` refspec, and CLI/MCP PR creation must use the same explicit
  head.
- Merge authorization binds `phase: "sdlc-merge-pr"`, the exact branch, current
  promoted/reviewed HEAD, explicit PR number or URL, explicit user request,
  passing checks/review/UAT, expiry, and entire canonical single-action
  head-matched CLI command. Active-run GitHub merge MCP writes are denied.
- Quoted documentation searches that mention guarded commands remain
  read-only and allowed.
- Expired or removed authorization files must deny again.
- Registered integration and worker worktrees outside the original checkout
  remain inside the active SDLC policy boundary. Identity drift must deny
  sensitive Git actions, and execution authorization must match action,
  worktree, branch, Git common directory, expected HEAD, expiry, and target.

## Stop Continuation

Stop cases:

- No active run.
- Complete, paused, blocked, or human-input state.
- Max iteration or retry budget exceeded.
- No-progress guard triggers.
- Merge-ready state without explicit merge request.

Continuation cases:

- Incomplete current feature.
- `next_recommended_skill` points to another phase.
- All features committed but UAT has not passed.
- Critical or pause/no-PR steering is present.
- UAT failed with an addressable classification.

Continuation prompts must say to use the prompt-bound `$sdlc-start run`
action, include current feature, current phase, next
recommended skill, and instructions to read local state first, avoid
locked-plan edits, and persist evidence before stopping. They must not expose
prompt IDs, run IDs, prompt bodies, or private snapshot paths. An unfinished
unbound run must stop with `WORKFLOW_UPGRADE_REQUIRED`.

## Prompt Workspace

Verify:

- `workspace init` works before Git initialization, leaves the project tree
  unchanged, creates one starter only when empty, and preserves prompt bytes,
  mtime, revisions, and completed history on rerun.
- The generated editor workspace exposes private new-prompt and metadata-only
  history tasks. History is ordered by monotonic accepted activity and never
  exposes bodies, IDs, digests, snapshots, or private run paths.
- Exact manual rename repairs the binding and run mirror. Rename-plus-edit,
  stale copies, duplicate IDs, or crash-time mirror disagreement fail closed;
  Stop continuation uses the repaired filename.
- Initializing Git later does not change the private workspace identity.
- New, unchanged active, edited active, unchanged completed, and edited
  completed prompts produce the canonical new/resume/steering/
  `ALREADY_COMPLETE`/new-run transitions.
- Each changed digest creates one adjacent immutable revision and exactly one
  steering entry; repeating an unchanged revision creates no duplicate.
- One unfinished prompt owns an exact project scope; conflicts, traversal,
  foreign paths, symlinks, unsafe modes, malformed UTF-8/frontmatter, oversize
  prompts, obvious secret material, and snapshot tampering fail closed.
- Unfinished unbound history returns `WORKFLOW_UPGRADE_REQUIRED`; completed
  unbound history remains readable.

## Golden Path

This is the unchanged lightweight no-flag fixture. It must not inspect or
mutate Docker, browser state, or three-tier lifecycle state.

Use a disposable Python project that validates a Nebius-style resource name:

- lowercase letters, numbers, and hyphens only
- starts with a letter
- 3 to 32 characters
- structured validation errors
- tests and evaluation evidence

Run the SDLC skills in order through local disposable state. Passing evidence
requires committed requirements/design, a locked local `TASK-*` graph,
execution preparation, tests before dependency-wave implementation,
one agent/branch/worktree per safe task, ordered merge commits, auto-steering
evidence, validation evidence, test evidence,
evaluation evidence, documentation update evidence when docs changed, exact
ff-only project promotion after evidence passes, non-force resource cleanup,
UAT evidence from the promoted checkout, and no private state committed.

For a nested monorepo project, verify all claims, worker `scope_cwd`, staged
paths, and committed paths remain inside the initialized folder. Exercise a
confirmed interrupted-worker transfer, a resource-free future-wave replan, a
rejected staged-secret attempt, and the fake-process sequential fallback. In a
managed outer worktree, verify the `agentic-sdlc` v4 lease blocks outer
integration, tracks all internal resources and promoted heads, survives every
external-first promotion persistence boundary, rejects stale local
promoted/released state, releases to an exact terminal receipt only after final
alignment/UAT/docs with a clean exact head, then allows only the recorded
primary path plus exact local `$worktree integrate` handoff. Verify the
coordinator and Stop hook stop for a fresh explicit user invocation from that
primary checkout, then record source-integration proof only after that separate
action.
For Task Implementer coexistence, verify replanning extends the active lane
generation's claims before replacement state is written, and verify differently
keyed external database, Kubernetes, Terraform, migration, and publication
domains collide through class-wide sentinel claims across separate lanes.
Keep live Codex execution `PARTIAL` when binary/auth/capacity is
unavailable rather than treating deterministic fake-process proof as live proof.

## Opt-In Three-Tier Live Profile

Use only for explicit `--create`, `--create --keep`, `--resume`, or
`--destroy`; read
`references/three-tier-live.md` for the authoritative workflow.

Verify before mutation:

- the no-flag deterministic verifier has no FAIL
- Docker Engine, Docker Compose, Git, source-installed parity, and canonical
  Google Chrome are available; the helper launches a fresh process group with
  a new verifier-owned user-data directory and marker, and a real Computer Use
  `get_app_state` must expose the exact marker before every action
- one valid owned verification root exists; if it has an active lifecycle,
  preflight succeeds before its exact ownership-checked cleanup, and cleanup
  completes before a fresh lifecycle is created
- the verification ID returned by prepare fences every later mutating helper
  action, and every Compose action runs through the helper while it holds the
  lifecycle lock; a superseded invocation fails before its next mutation
- the selected dynamic web port resolves to loopback only
- the managed starter renders through the canonical prompt renderer and is
  accepted by prompt intake as revision `r0001`
- fixed public base images can be pulled with the owned empty Docker CLI config
  without exporting that config to Compose

Build and test `three-tier-task-board-v1` through the existing prompt-bound
Agentic SDLC workflow. Require a browser GUI, Django/Gunicorn web/API server,
and PostgreSQL database, running as exactly two labelled Compose containers.
Do not host-publish the database port.
Verify the recorded web and database IDs have canonical Compose service labels
`web` and `db`; do not rely on repeated-argument order.

Require semantic evidence for requirements, context, design, steering, plan,
execution preparation, test-first development, implementation, validation,
unit/API/database/vertical tests, evaluation, documents, alignment, commit,
local ship, computer-use UAT, and the final document pass. Bind all evidence to
the clean promoted SHA. Reject placeholder `{"result":"pass"}` artifacts,
generic evidence reused across test classes, missing phases, screenshots as the
only GUI oracle, and stale worker/integration SHAs.

GUI evaluation and UAT must use `harness: computer-use` against only the fresh
verifier-owned Chrome instance. Immediately before the first navigation in
evaluation and again before UAT, require a fresh browser `get_app_state` whose
accessibility state contains the exact verification marker while
the console is unlocked unless the current Codex surface explicitly confirms
locked Computer Use is enabled for this session. A normal target window must
be visible, unminimized, foreground, and on the current macOS Space. Refresh
accessibility state after every successful action. Test blank input, create,
refresh persistence, complete, active/completed filters, and service restart
without volume deletion. Correlate the same record ID, title, and completion
state through GUI, API, and database. Require five distinct sanitized
screenshots.

Classify `cgWindowNotFound` or another just-in-time visibility failure as
`ENVIRONMENT_DEFECT` at `pre-navigation-window-capture`, explicitly recording
that no GUI navigation or action was attempted. If any Computer Use call hangs,
times out, or stops responding across browsers, stop all further Computer Use
calls for that attempt. Do not attempt `list_apps`, new-window recovery,
repeated browser retries, or browser/service restart through the same unhealthy
path. Exact PID/process-group cleanup remains allowed without Computer Use and
must fail closed when its executable/profile identity cannot be revalidated.
Fresh-session or service recovery remains a separate explicitly authorized
action.

The report must include logical/container layer inventory, Docker and browser
versions, project/report paths, baseline/promoted SHAs, resolved web/API/health
URLs, internal database endpoint, all phase and test outcomes, exact owned
container/network/volume/image IDs, UAT result, and cleanup/retention state.

Default create closes the exact verifier-owned Chrome process group and destroys the exact owned
project, raw evidence/private state, two containers, network, database volume,
and built web image even after failure. Any cleanup failure is FAIL. Create plus
keep preserves those resources and reports `KEPT`. Standalone destroy validates
the exact Chrome process identity, then both Docker ownership labels for every
alias, canonicalizes and deduplicates Docker identities, preserves a cumulative
retry ledger, retains sanitized reports/lifecycle history, and returns
`ALREADY_DESTROYED` when no active application exists. Existing Chrome
instances are never cleanup targets.

## Deterministic Capability Lanes

The verifier must execute named regression tests and report independent stable
capabilities for the public interface, prompt workspace/history/rename/
lifecycle, exact execution scope, worker-session recovery, future replan,
secret persistence gate, sequential fallback, managed outer lease, Task
Implementer interoperability, steering continuation, and verifier self-tests.

The composed managed-outer regression must start from the real `worktree`
manager, select a nested project folder, run the execution coordinator through
promotion, prove publication is blocked while the Agentic SDLC lease is active,
release only after final alignment/UAT/docs evidence, and then acquire the
publication reservation.

## Private Live Evidence

The optional manifest defaults to
`~/.codex/sdlc-verification/live-results.json` and uses
`agentic-sdlc/verification-live-results-v3` from
`assets/live-results.schema.json`. It binds:

- the verification ID emitted in private `verification-context.json`
- the exact nested selected-project path
- the preserved baseline and final Git heads with clean descendant proof
- the seven lanes `golden-path`, `idempotency`, `change-request`,
  `failure-routing`, `auto-steering`, `documentation-update`, and
  `steering-continuation`
- PASS, FAIL, or PARTIAL plus one canonical result for every lane
- the exact evidence profiles used by each skill result: `deterministic`,
  `lightweight`, `three-tier`, or `safety`

Only `lightweight` and `three-tier` are external source profiles in the live
manifest. Deterministic and safety claims are verifier-owned capabilities and
must never be manufactured as profile wrappers.

The manifest and referenced artifacts must be private regular files under the
verification root. Reject symlinks, path escapes, stale identity, dirty Git
state, permissive manifest modes, invalid fields, and PASS without evidence.
Every lane stores its result under its own `evidence/<lane>/` directory.
Every assertion is a `{passed, evidence}` record whose evidence entries name
private owner-local artifacts and their SHA-256 digests. Skill results carry
the manifest verification ID, baseline/final heads, and exact required evidence
profiles. Each profile result declares its exact allowed source schema and at
least one private source artifact copied and hashed through
`scripts/collect_live_evidence.py`. The first source is JSON whose schema and
identity fields must match the profile declaration. That structural check does
not grant PASS: a source-specific semantic validator must also prove the full
profile result. Reject assertion labels, bare booleans, profile status without
source artifacts, source-schema mismatch, digest drift, stale skill
identity, cross-owner artifact reuse, or a PASS whose required profile is not
registered with passing provenance. Golden-path PASS
requires a real descendant commit with a selected-scope change; an unchanged
baseline/final pair or one generic artifact reused across lanes is invalid.
Inspect every commit from baseline through final and reject any path ever
touched outside the selected nested project or in private SDLC state, including
paths deleted again before the final tree. Never copy evidence bodies into the
report. The verification root itself is private `0700` on POSIX.

A clean canonical flat fixture from an older verifier may migrate once to the
nested shape only when its tracked tree is exact and it has no remote. Any
unknown tracked, staged, or untracked content fails closed and is preserved.
The verification root must be dedicated, outside the source repository,
non-symlinked, and carry the private ownership marker before chmod or writes;
the disposable Git root must carry the exact fixture marker. Symlinked fixture
components,
installed skill roots, configured hook payloads, or report paths fail closed
before mutation. Invalid UTF-8 becomes a reported failure rather than a crash.
A deterministic subprocess timeout becomes a named FAIL check; it must not
prevent report generation. A custom report path must remain under the private
verification root.

Capability regression subprocesses use a 120-second default timeout. The
measured slow worktree and Task Implementer wave matrices use bounded 300- and
900-second timeouts respectively, so their full crash/recovery coverage is not
misreported as a workflow failure.

## Idempotency And Change Request

Repeat `run` with no prompt changes and verify no duplicate revisions, specs, plans, tests,
commits, or evidence.

The golden-path evidence must also prove that every persisted/emitted phase
skill name is canonical. In particular, commit-to-UAT handoff must name
`sdlc-uat-tests`; the short alias `sdlc-uat` is invalid evidence.

Then apply this change request:

```text
Allow underscores when explicitly configured.
```

Verify stable `REQ-*` and `FEAT-*` IDs, preserved old locked plan, a new plan
version only when needed, scoped test/code changes, refreshed evidence, and a
new exactly sealed and promoted feature tip after evidence passes.

## Failure Loop

Inject controlled validation, test, bad-test, design, spec-gap, and environment
failures one at a time. Each failure must be classified by
`sdlc-classify-failure`, routed to the earliest responsible phase, repaired,
and rerun without blind retry.

## Steering And Continuation

Use this steering instruction:

```text
Pause after the current feature. Do not create a PR.
```

Verify `sdlc-start` reads `STEERING.md`, Stop continuation respects the
instruction, no PR is created, and clearing steering allows resume.

Edit the same prompt with a requirements change, design change, and docs update,
repeating `run` after each change. Verify `sdlc-auto-steering` records each revision in private
steering state, redacts unsafe material, assigns a disposition, and routes
product-truth changes to `sdlc-create-requirements` or `sdlc-create-design`
before implementation treats them as true.

Verify `sdlc-update-documents` records documentation evidence after evaluation
or UAT when project-facing docs changed.

For long-running continuation, verify local state survives context loss,
feature isolation is preserved, and max-iteration/no-progress guards prevent
runaway execution.

## GUI And TUI Evidence

GUI and TUI checks are safe local tests, but both are required for an exact
20-skill PASS:

- GUI: use the three-tier Computer Use profile and require semantic GUI
  assertions, API/database correlation, and restart persistence. Missing GUI
  capacity before an attempt is PARTIAL.
- TUI: use the lightweight fixture's documented terminal flow and require a
  private transcript, exit status, expected output, invalid-input behavior,
  and no persistent side effects.

## Final Status Rules

- PASS: every required deterministic capability, all seven live lanes, and all
  20 skill rows pass with source-specific machine-validated evidence from their
  required profiles. Generic digest-backed evidence can reach PARTIAL only.
- PARTIAL: no required check failed, but optional hooks or one or more live
  lanes are missing or partial.
- FAIL: any required deterministic check fails or any supplied live lane or
  evidence-integrity check fails.

The v3 manifest also requires an exact 20-skill matrix. Every row must bind to
the verification identity and Git heads, declare its exact evidence profiles,
and use owner-local digest-backed assertion artifacts. Cross-layer GUI/API/
database and restart claims require `three-tier` provenance; lightweight
terminal and prompt-bound claims require `lightweight` provenance.
`sdlc-merge-pr` uses the deterministic explicit-authorization hook result plus
safety evidence and never performs a real merge. `sdlc-tui-test` requires its
own disposable terminal evidence and is not inferred from continuation state.
