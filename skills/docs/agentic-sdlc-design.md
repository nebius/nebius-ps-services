# Agentic SDLC Design

## Table Of Contents

- [Purpose](#purpose)
- [Core Concepts](#core-concepts)
  - [Agent model](#agent-model)
  - [Agent-selected phases](#agent-selected-phases)
  - [Committed project truth](#committed-project-truth)
  - [Private local run state](#private-local-run-state)
  - [Feature execution plane](#feature-execution-plane)
  - [Hooks as guardrails](#hooks-as-guardrails)
  - [MCP servers and external capabilities](#mcp-servers-and-external-capabilities)
- [Requirements, Design, And Project Instructions](#requirements-design-and-project-instructions)
- [Workflow](#workflow)
- [Workflow Verification](#workflow-verification)
  - [Verification principles](#verification-principles)
  - [Quick preflight test](#quick-preflight-test)
  - [Full workflow test](#full-workflow-test)
  - [Real three-tier application test](#real-three-tier-application-test)
  - [Report interpretation](#report-interpretation)
  - [Fix and rerun policy](#fix-and-rerun-policy)
- [Skill Responsibilities](#skill-responsibilities)
  - [`sdlc-create-requirements`](#sdlc-create-requirements)
  - [`sdlc-start`](#sdlc-start)
  - [`sdlc-gather-context`](#sdlc-gather-context)
  - [`sdlc-create-design`](#sdlc-create-design)
  - [`project-agent-instructions`](#project-agent-instructions)
  - [`sdlc-auto-steering`](#sdlc-auto-steering)
  - [`sdlc-create-plan`](#sdlc-create-plan)
  - [`sdlc-prepare-execution`](#sdlc-prepare-execution)
  - [`sdlc-tdd`](#sdlc-tdd)
  - [`sdlc-implement-plan`](#sdlc-implement-plan)
  - [`sdlc-validate-codes`](#sdlc-validate-codes)
  - [`sdlc-unit-tests`](#sdlc-unit-tests)
  - [`sdlc-evaluate`](#sdlc-evaluate)
  - [`sdlc-update-documents`](#sdlc-update-documents)
  - [`sdlc-gui-test`](#sdlc-gui-test)
  - [`sdlc-tui-test`](#sdlc-tui-test)
  - [`sdlc-classify-failure`](#sdlc-classify-failure)
  - [`sdlc-align-specs`](#sdlc-align-specs)
  - [`sdlc-commit`](#sdlc-commit)
  - [`sdlc-uat-tests`](#sdlc-uat-tests)
  - [`create-pr`](#create-pr)
  - [`review-pr`](#review-pr)
  - [`sdlc-merge-pr`](#sdlc-merge-pr)
- [Failure And Retry Model](#failure-and-retry-model)
- [Guarded Git Actions](#guarded-git-actions)
- [Resume And Idempotency](#resume-and-idempotency)
- [Boundaries](#boundaries)

## Purpose

The Agentic SDLC workflow is a skill-driven state machine for turning a user
prompt into committed implementation, product-level UAT, pull request creation,
PR review, and an explicitly requested merge.

There is no workflow CLI. The agent chooses the next SDLC skill, committed
project documents hold product truth, private local state records execution,
MCP servers provide external capabilities when needed, and optional hooks
enforce only non-negotiable runtime guardrails.

## Core Concepts

### Agent model

The workflow uses the Codex agent as the runtime decision maker. Skills are
phase contracts, instructions, templates, and validation rules that the agent
loads at the right point in the lifecycle; they are not long-running services.

The parent agent is the feature coordinator: it owns phase decisions, shared
execution state, integration, combined validation, promotion, and the final
report. During implementation, every safe `TASK-*` uses one fresh task agent
with its own branch and private Git worktree. A task agent owns only its
immutable assignment, declared write claims, focused evidence, review, and one
direct-child commit. Task agents never select SDLC phases or mutate shared
coordinator state directly. MCP servers are tools an agent can call through the
selected phase skill.

### Agent-selected phases

The SDLC is advanced by Codex choosing skills, not by a standalone command
runner. `sdlc-start` is the coordinator skill. It reads the committed specs and
private local run state, selects one current feature, and returns exactly one
next recommended skill.

All `sdlc-*` skills set `allow_implicit_invocation: false` in
`agents/openai.yaml`. The external `sdlc-workflow-test` verifier is the sole
non-phase exception to the prefix convention. Operators and continuation
prompts enter the workflow explicitly through
`$sdlc-start run <prompt-path-or-unique-filename>`, and the coordinator records the next
recommended phase skill in local state. This keeps workflow phases from being
selected by ordinary prompt matching outside an active Agentic SDLC run.

Every phase skill owns a narrow responsibility. For example,
`sdlc-create-requirements` owns `docs/requirements.md`, `sdlc-create-design`
owns `docs/design.md`, `project-agent-instructions` owns the conditional
generated project-root `AGENTS.md`, `sdlc-create-plan` owns a locked private
task graph, and `sdlc-prepare-execution` owns the persistent integration
resource.
`sdlc-implement-plan` owns task waves and ordered integration, while
`sdlc-commit` owns final sealing and exact local promotion after evidence passes.

### Committed project truth

The always-present committed SDLC truth inside a target project is:

- `<project>/docs/requirements.md`
- `<project>/docs/design.md`

After both documents validate, `project-agent-instructions` decides whether
durable project-specific operating rules add to the inherited instruction
chain. Only when that evidence gate passes does committed project truth also
include:

- `<project>/AGENTS.md`

A missing file with no meaningful project-specific delta is a valid
`not-needed` outcome. Other SDLC artifacts, including the decision manifest and
receipt, are local runtime state and must not be committed.

Ownership is strict:

- Only `sdlc-create-requirements` writes `docs/requirements.md`.
- Only `sdlc-create-design` writes `docs/design.md`.
- Only `project-agent-instructions` may create or refresh a provenance-owned
  generated project-root `AGENTS.md`; human-owned instruction files are
  preserved.
- Other skills route changes back to the owning skill instead of editing those
  files directly.

### Private local run state

Users first create a durable private prompt workspace and then run the workflow
through exactly two public coordinator actions:

```text
$sdlc-start workspace init [project-folder]
$sdlc-start run <prompt-path-or-unique-filename>
```

Managed prompts use `agentic-sdlc/prompt-v1`. Editing the same prompt and
repeating `run` is the only steering path; there is no bare `$sdlc-start`
resume action. The generated editor workspace provides private new-prompt and
metadata-only history tasks; these do not expand the public interface. Exact
manual renames preserve identity and update the run mirror, while rename plus
content edits and stale/duplicate prompt copies fail closed. Execution state
lives under:

```text
~/.codex/sdlc-runs/<project-id>/<run-id>/
```

The project-level SDLC state also has `workspace.json`, `activity.json`, a
private prompt lock, managed prompt files, an `active-run.json` pointer, and an
`active.lock` lock file under `~/.codex/sdlc-runs/<project-id>/`. The active
run directory stores the active feature, run metadata, checkpoints, feature
queue, fingerprints, steering, context packs, locked plans, evidence,
screenshots, transcripts, failure logs, history, and short-lived authorization
files. It is the recovery source when conversation context is lost.

Important files include:

- `prompts/*.md`: editable durable prompts outside Git.
- `<run-id>/prompt.json` and `inputs/rNNNN/prompt.md`: one run binding and its
  immutable accepted revisions.
- `run.json`: active run metadata and run-level status.
- `current-state.json`: current feature, phase, status, retry counts, and next
  recommended skill.
- `feature-queue.json`: ordered feature queue derived from `docs/design.md`.
- `fingerprints.json`: requirement, design, context, plan, steering, and
  documentation fingerprints.
- `checkpoints/checkpoint-*.json`: append-only state snapshots.
- `checkpoints/latest.json`: pointer to the newest complete checkpoint.
- `STEERING.md`: active-run steering inbox and ledger, not a second
  requirements file.
- `steering/auto-steering.json`: machine-readable steering dispositions and
  compact active reminders.
- `context/FEAT-*.context.md`: compact context packs for design and planning.
- `plans/FEAT-*.plan.vN.md` and `.lock`: private locked feature plans.
- `execution/FEAT-*/coordinator.json`: schema-v5 feature execution identity,
  exact base/integration SHAs, wave pointers, and promotion state.
- `execution/FEAT-*/waves/`, `tasks/`, `assignments/`, `incoming-handoffs/`,
  `sessions/`, `results/`, and `journals/`: separate private records for
  authoritative capacity batches, immutable prior-task context, exclusive
  session ownership, and parallel result isolation.
- `execution/interop.json`: optional run-level managed-outer-worktree lease
  identity, promoted head, and release state.
- `repairs/FEAT-*/events/`, `diagnoses/`, and `classifications/`: immutable
  commit-bound failure records, causal handoffs, and deterministic routes.
- `repairs/FEAT-*/repair-control.json`: atomic projection of the active stable
  blocker, route history, invalidations, attempts, active time, and feature
  dispatch budget.
- `repairs/FEAT-*/repair-journal.jsonl`: append-only crash-safe repair
  transitions.
- `worktrees/FEAT-*/integration/` and `worktrees/FEAT-*/waves/`: persistent
  integration and per-task worker checkouts under the private run root.
- `evidence/`: validation, test, evaluation, documentation, UAT, PR, review,
  merge, and commit evidence.
- `evidence/FEAT-*/documents.md` and `evidence/uat/documents.md`:
  documentation update evidence.
- `history/iteration-*.md`: append-only state-transition history.
- `permissions/`: short-lived authorization files for guarded commit, PR,
  merge, and action-scoped raw execution Git operations.

`sdlc-start` resumes from the latest checkpoint and current state. It does not
depend on conversation memory to know the current phase, feature, retry count,
or next skill.

During an active run, every user prompt submitted to steer the in-flight SDLC
work is recorded in private steering state. `STEERING.md` keeps the
human-readable inbox and ledger; `steering/auto-steering.json` keeps the
machine-readable dispositions and compact active reminders. Entries that
change requirements or design still route through `sdlc-create-requirements`
or `sdlc-create-design` before they become implementation truth.

### Feature execution plane

One active feature has one persistent integration branch/worktree from
pre-TDD preparation through final promotion. The project promotion branch is
either the current named non-default branch or a deterministic `feature/sdlc-*`
branch created from the verified symbolic `origin` default. It stays clean and
fixed at its recorded base SHA during TDD, implementation, validation, tests,
evaluation, documentation, and spec alignment.

The locked plan contains stable `TASK-*` records with requirement IDs,
dependencies, exact/prefix write claims, conflict domains, focused validation,
done criteria, and rollback/stop conditions. `sdlc-prepare-execution`
recomputes deterministic earliest-fit dependency waves. Tasks share a wave only
when their dependencies are satisfied and their write claims and conflict
domains are pairwise disjoint. Unknown ownership, shared interfaces or schemas,
migrations, dependency manifests, infrastructure identities, exclusive test
resources, and external mutations serialize.

For each task in the active capacity batch, the coordinator creates one branch,
one locked private worktree, one immutable assignment, and one digest-bound
incoming handoff, then dispatches one fresh task agent. Only the first batch of
the first wave has no predecessors; later assignments contain structured
evidence from all earlier completed waves and batches. A process-safe transition
lock, atomic hashed-session claims, and expected-attempt recovery guards prevent
session or task ownership reuse. Workers return explicit summary, decisions,
risks, validation, and review evidence and produce one direct-child commit. The
coordinator verifies digests, paths, Git identity, and ancestry, opens the next
batch only after the active batch commits, merges tasks in stable task order
with retained worker commits and explicit `--no-ff` merge commits, runs combined
evidence at the exact integration tip, and non-force-cleans only clean reachable
worker resources.

After all downstream evidence passes, `sdlc-commit` seals at most one final
integration commit, verifies the original project checkout is unchanged, and
fast-forwards it to the exact sealed integration SHA. Only then does it
non-force-clean the integration worktree and branch. UAT and PR work run from
the promoted project checkout.

The private transition helper is not a workflow CLI: it performs narrow,
idempotent state transitions selected by the phase skills. It never chooses the
next phase or replaces the prompt-bound `sdlc-start run` coordinator action.

### Hooks as guardrails

The optional Agentic SDLC hook source lives in:

```text
sdlc-start/assets/hooks/
```

The bundle contains:

- `pre_tool_use_sdlc_policy.py`
- `stop_sdlc_continue.py`
- `lib/__init__.py`
- `lib/sdlc_policy.py`
- `lib/sdlc_state.py`
- `tests/test_sdlc_hooks.py`

Hooks are guardrails, not the workflow engine. Phase selection remains owned by
`sdlc-start`.

The Stop hook can route an active run back through the explicit prompt-bound
`sdlc-start run` command. It stops instead of continuing when the
run is complete, paused, blocked, waiting on human input, over the iteration or
retry budget, or making no progress after repeated continuation attempts. It
can continue to the bound `sdlc-start run` command when local state recommends another phase,
steering needs to be refreshed by `sdlc-auto-steering`, all features are
committed and UAT still needs to run, or UAT failed with an addressable
classification. It never auto-continues into `sdlc-merge-pr`; merge requires
an explicit user request.

The PreToolUse hook does not deny filesystem targets by path. File reads,
writes, updates, deletes, and moves may target repository files, outside-repo
files, credential directories, Codex runtime files, global `AGENTS.md`, locked
SDLC plans, and private SDLC state when the operator needs that flexibility.
Because Codex matches a user-level PreToolUse registration by tool name before
the hook can inspect local run state, the hook immediately allows calls when no
active SDLC run covers the current working directory and its registration does
not display a static SDLC status message. During an active run, the hook can
still deny unsafe content or action shapes such as secret-bearing shell, patch,
or MCP payloads, destructive Bash commands, destructive Git commands,
protected-branch commit or push attempts, force pushes, force cleanup, and
guarded Git or GitHub actions without valid short-lived authorization.
Dangerous-shell pattern matching applies only to Bash tool payloads; source
patches containing Dockerfile or documentation command text remain governed by
patch target, secret, and spec checks. Registered
integration and worker worktrees remain inside the
active run even though they live outside the original checkout. For sensitive
Git operations the hook verifies their Git root/common directory, branch,
recorded HEAD, and action-scoped authorization. Ordinary outbound network
commands are not restricted by the hook unless they match those unsafe content
or action checks.

Ordinary outbound network commands remain allowed unless another guarded
content or action rule applies.

The hook bundle is source code in the skills repository. Installed copies under
`$CODEX_HOME/hooks` are runtime artifacts and can drift. Hook fixes should be
made in `sdlc-start/assets/hooks/`, validated locally, and then synced
deliberately with the installer.

### MCP servers and external capabilities

MCP servers are capability providers, not the state machine. Phase skills use
them when the feature requires external context or interaction:

- official docs and package documentation for version-sensitive behavior
- Computer Use, Browser, or Playwright for GUI evaluation according to the
  declared acceptance harness
- GitHub for PR creation, PR review, checks, and merge readiness
- Slack, Confluence, Jira, or internal knowledge sources for approved context
- `nebius-grafana-query` for narrowly scoped, read-only runtime facts when
  troubleshooting or evaluation has a decision-changing observability question
- other domain MCP servers when a feature needs that external system

The selected SDLC skill remains responsible for deciding what evidence is
needed, how to use the MCP result, and where to store the summary. Raw logs,
secrets, private tokens, and broad copied source material do not belong in
committed docs or durable state. When the optional hooks are installed, GitHub
PR or merge MCP writes require the matching short-lived authorization, while
other external write MCP calls are gated by active SDLC state plus secret and
write-target checks.

The observability provider performs one lazy datasource readiness check for an
evaluation run, then uses a cumulative six-query fast allowance and a
four-query decision-specific deep allowance. Each embedded provider invocation
attempts at most one pre-admitted query, then returns for a consumer ledger
update. Troubleshooting deep queries distinguish hypotheses; evaluation deep
queries may only distinguish named attribution, coverage, or dependency
interpretations of one predefined gate. The provider returns redacted
structured facts, data gaps, and query cost; it does not diagnose root cause or
grade acceptance. A failed readiness check disables the path for that run
without setup or repeated checks. Each response returns the connectivity state
and total, fast, and deep remaining budgets needed for the next call. Invalid
relevance, authority, selector, window, or budget is rejected before any
external call and is not classified as an environment outage.

## Requirements, Design, And Project Instructions

The two committed product-truth documents are created from templates in their
owning skills:

- `sdlc-create-requirements/assets/templates/requirements.md.template`
- `sdlc-create-design/assets/templates/design.md.template`

`sdlc-create-requirements` uses the requirements template when
`<project>/docs/requirements.md` does not exist. The template defines the
front matter, source prompt, problem, desired outcome, users, success
definition, constraints, environment, external systems, stable `REQ-*` blocks,
acceptance criteria, negative criteria, validation method, test method,
evaluation method, optional Live Experiment Environment details, open questions,
decision log, and change log. The Live Experiment Environment section records
status, non-production confirmation, safe access references, allowed and
prohibited agent actions, test data, reset instructions, approvals, and
evidence limits without storing secret values.

The requirements template also records:

- `created_by_skill: "sdlc-create-requirements"`
- `updated_by_skill: "sdlc-create-requirements"`
- a user edit policy that routes updates through `sdlc-create-requirements`
- an ID policy that forbids renumbering existing requirement IDs

`sdlc-create-design` uses the design template when
`<project>/docs/design.md` does not exist. The template defines the front
matter, source requirements link, design summary, technology choices,
architecture sections, stable `FEAT-*` feature blocks, requirements covered,
context evidence, end-to-end feature flow, layer map, implementation
boundaries, TDD success criteria, validation plan, test plan, evaluation plan,
done definition, UAT strategy, risks, open design questions, decision log, and
change log.

The design template also records:

- `created_by_skill: "sdlc-create-design"`
- `updated_by_skill: "sdlc-create-design"`
- `source_requirements: "docs/requirements.md"`
- a user edit policy that routes updates through `sdlc-create-design`
- an execution plan policy stating that plans are local runtime artifacts and
  must not be committed
- an ID policy that forbids renumbering existing feature IDs

Project instructions intentionally have no fill-every-section template.
`project-agent-instructions/references/decision-contract.md` defines the
evidence gate, generated section allowlist, provenance marker, ownership
rules, and deterministic inspect/apply/verify helper. The skill runs only
after both specifications validate. It creates a concise selected-project
`AGENTS.md` only for durable, project-specific, actionable rules not already
provided by inherited instructions; it omits empty sections, task state,
acceptance criteria, architecture essays, generic advice, and reusable
procedures.

## Workflow

The high-level workflow is:

![Agentic SDLC task-to-skill workflow: the numbered golden path maps every task
to its owning skill, followed by conditional failure classification,
troubleshooting, corrective planning, revalidation, design admission, and
precise stop branches.](images/agentic-sdlc-workflow.svg)

The SVG is the canonical diagram source. Use the
[generated PNG rendering](images/agentic-sdlc-workflow.png) where SVG rendering
is unavailable.

The implementation state schema uses this phase order:

1. requirements
2. context
3. design
4. project-agent-instructions
5. sdlc-auto-steering
6. plan
7. execution preparation
8. sdlc-tdd in the integration worktree
9. implementation dependency waves
10. validation at the recorded integration HEAD
11. test at the recorded integration HEAD
12. evaluation at the recorded integration HEAD
13. sdlc-update-documents in the integration worktree
14. sdlc-align-specs in the integration worktree
15. sdlc-commit final seal, ff-only promotion, and cleanup
16. uat from the promoted project checkout
17. create-pr
18. review-pr
19. sdlc-merge-pr, only after explicit user request

`sdlc-auto-steering` also runs at the start of each feature loop, or whenever
`sdlc-start` sees new steering input, stale steering fingerprints, or
unresolved steering dispositions. `sdlc-update-documents` runs in feature scope
after evaluation and may run again in run scope after UAT when UAT or final
steering changes user-facing docs before PR creation.

`sdlc-classify-failure` is the routing mechanism for failed phases rather than
a normal happy-path phase. Proven mechanical causes take the shortest route to
their owning phase. Ambiguous, persistent, intermittent, or cross-boundary
failures route conditionally to `troubleshoot`; its structured diagnosis always
returns through classification before a repair owner is selected.
`troubleshoot` is therefore required runtime support but is absent from the
golden phase sequence. Missing decisive evidence, competing hypotheses,
policy/permission/human decisions, and exhausted budgets stop instead of
guessing.

## Workflow Verification

`sdlc-workflow-test` is the external verification skill for this workflow. It is
not an SDLC phase despite its requested `sdlc-` prefix. Use it to verify the
workflow against this design, inspect required phase-skill discovery, check
hook configuration, run disposable PreToolUse and Stop hook fixture tests, and
write a verification report to `~/.codex/sdlc-verification/report.md`. Its
no-flag behavior remains the lightweight verifier. Explicit `--create`,
`--create --keep`, `--resume`, and `--destroy` select a separately owned real
three-tier profile; they do not change the public `$sdlc-start` workflow
interface.

### Verification principles

The design document is the workflow contract. The verifier must compare source
and installed SDLC skills, hook configuration and payload parity, disposable
run state, deterministic capability results, and validated private live
evidence back to this document instead of relying on conversation memory or
stale prior reports.

The verifier must remain safe and idempotent:

- use a disposable verification project only
- keep private verification state out of committed repositories
- avoid changing installed skills, hooks, hook trust, or agent configuration
- avoid pushing, opening real PRs, merging, publishing, or touching production
  resources
- route the disposable golden-path run through the normal SDLC phase skills
  instead of creating a workflow CLI
- for live three-tier cleanup, resolve targets from an exact private lifecycle
  inventory and two ownership labels, never from a name prefix

The verifier writes only verification-owned state under:

```text
~/.codex/sdlc-verification/
```

That root must be a dedicated non-symlinked directory outside the source
repository. The canonical root may be adopted in place; a custom root must be
new or already carry the verifier's private ownership marker. The verifier
must reject broad or unowned roots before chmod or writes. Its disposable Git
repository must have no remote and must carry the exact public fixture marker;
only an exact clean legacy flat fixture with the expected tracked tree may be
migrated once.

### Quick preflight test

Use the quick preflight when the SDLC skills, hook source, hook configuration,
or this design document changed and you need a safe readiness check:

```text
$sdlc-workflow-test Verify the Agentic SDLC workflow against docs/agentic-sdlc-design.md and write a safe report.
```

The skill runs the deterministic helper from the skills repository:

```bash
python3 sdlc-workflow-test/scripts/verify_agentic_sdlc.py
```

The preflight must verify and record:

- global required phase-skill folders, `SKILL.md` front matter, and explicit-only
  `agents/openai.yaml` invocation policy
- `sdlc-auto-steering` and `sdlc-update-documents` discovery, metadata, and
  placement in the workflow contract
- `sdlc-prepare-execution` discovery, task-graph/state-schema contract, and
  deterministic scheduler plus real-Git lifecycle tests
- installed `worktree`, installed `nebius-grafana-query`, installed
  `project-agent-instructions`, and conditional `troubleshoot` availability plus
  source-installed parity for every required SDLC skill, both runtime support
  classes, and `sdlc-workflow-test`
- named regression capabilities for prompt workspace/history/exact manual
  rename/lifecycle; execution scope, sessions, `task-recover`, resource-free
  `replan-future`, secret gates, and sequential fallback; Task Implementer
  interoperability; and prompt-bound steering continuation
- deterministic failure-event, diagnosis, repair-control, design-admission,
  corrective-plan, full task-definition digest, and Stop-hook route contracts
- a composed real-Git test that selects a nested folder in a managed outer
  worktree, runs schema-v5 execution through promotion, proves the v3 outer
  lease blocks publication, releases after final evidence, and then acquires
  the create-PR reservation
- duplicate SDLC skill-name detection
- optional PreToolUse and Stop registration; absence is WARN/PARTIAL, while
  malformed configuration, a non-canonical configured entrypoint, source/
  payload mismatch, or unsafe behavior is FAIL
- preservation of non-SDLC hook boundaries such as `SessionStart` and
  `UserPromptSubmit`
- PreToolUse allow and deny fixture cases
- registered integration/worker worktree detection, identity-drift denial, and
  exact action-scoped execution authorization
- Stop terminal cases: no active run, complete, paused, blocked, human input,
  max iteration, retry budget, no progress, and merge without explicit request
- Stop continuation cases through `sdlc-start`
- disposable-project creation and private-state staging checks
- private `verification-context.json` creation for the exact nested selected
  project and optional `agentic-sdlc/verification-live-results-v3` ingestion;
  the preserved context owns the baseline identity, and committed changes must
  remain inside the selected project without private SDLC state
- private `0700` verification-root permissions and ownership, a remote-free
  marked disposable Git root, exact clean one-time migration from the canonical
  flat fixture, fail-closed preservation of unknown content, and timeout-to-FAIL
  reporting
- report generation at `~/.codex/sdlc-verification/report.md`

A successful quick preflight may still return `PARTIAL` when the full
agent-driven golden path has not been completed. That status is expected until
the disposable workflow run, idempotency run, change-request run, failure-loop
run, and steering checks have evidence.

The verifier may ingest an optional private manifest with
`--live-evidence PATH`, defaulting to
`~/.codex/sdlc-verification/live-results.json`. It validates the verification
identity, exact selected-project path, clean baseline/final Git ancestry, seven
named live lanes, lane-specific private evidence under `evidence/<lane>/`, a
real selected-scope golden-path commit, every committed path across the full
baseline-to-final history, and schema shape. It rejects symlink redirects,
invalid UTF-8, an unchanged synthetic golden path, transient private or
out-of-scope commits that disappear from the final tree, and report paths
outside the private verification root. It never reads evidence bodies into the
report and never turns missing live execution into synthetic PASS.

### Full workflow test

Use the full workflow test before treating the Agentic SDLC system as validated
end to end. It must run only in the disposable verification project created
under `~/.codex/sdlc-verification/disposable-project/`.

The golden-path feature is intentionally small:

```text
Create a Python validator for a Nebius-style resource name:
- lowercase letters, numbers, and hyphens only
- starts with a letter
- 3 to 32 characters
- structured validation errors
- tests and evaluation evidence
```

Run the normal SDLC phase skills through the disposable project. Do not add a
new workflow CLI and do not let hooks orchestrate phases.

Required happy-path evidence:

- `sdlc-create-requirements` creates committed requirements with stable
  `REQ-*` IDs.
- `sdlc-start` creates or resumes private local run state and recommends one
  next skill.
- `sdlc-gather-context` records a compact context pack, including layer and
  boundary facts when the feature may span a vertical slice.
- `sdlc-create-design` creates committed design with stable `FEAT-*` IDs.
- `project-agent-instructions` records a verified conditional decision and
  creates or refreshes a selected-project `AGENTS.md` only when current
  evidence requires durable rules beyond inherited instructions.
- `sdlc-auto-steering` records active-run steering, classifies mid-run prompts,
  and derives compact reminders without changing product-truth docs.
- `sdlc-create-plan` writes a private locked task graph with dependencies,
  write claims, conflict domains, validation, done, and stop conditions.
- `sdlc-prepare-execution` recomputes waves and prepares the persistent private
  integration branch/worktree from the exact clean project base.
- `sdlc-tdd` records test intent in the integration worktree before
  implementation, including planned
  slice contracts and cross-layer validation targets when present.
- `sdlc-implement-plan` uses one fresh agent, branch, and worktree per safe task,
  preserves worker commits, creates ordered merge commits, runs combined
  evidence, and non-force-cleans worker resources.
- `sdlc-validate-codes`, `sdlc-unit-tests`, and `sdlc-evaluate` record passing
  evidence or route failures for classification, including slice boundary
  checks, slice coverage, and end-to-end slice observation when the locked plan
  defines one.
- `sdlc-update-documents` updates README, changelog, examples, or usage docs
  when implemented behavior requires it and records documentation evidence.
  Multi-layer behavior docs are backed by evaluated slice evidence.
- `sdlc-align-specs` confirms requirements, design, plan, implementation,
  documentation, slice evidence, and other evidence agree.
- `sdlc-commit` seals final integration changes, verifies all evidence and the
  recorded remote-default identity, removes clean reachable worker resources,
  and fast-forwards the unchanged project promotion branch to the exact
  integration tip under the common-Git-directory lock. It then removes the
  clean integration worktree and deletes its ref at the exact promoted SHA.
- `sdlc-uat-tests` records product-level UAT evidence after all features are
  committed.
- private SDLC state, plans, screenshots, transcripts, and evidence stay out
  of the disposable project Git index.

After the happy path, verify these recovery behaviors:

- rerun with no product change and confirm no duplicate requirements, design
  features, plans, tests, commits, or evidence
- repeat `run` with the unchanged managed prompt and confirm the same run and
  revision resume; edit the completed prompt and confirm a new run retains old
  history, while an unchanged completed prompt returns `ALREADY_COMPLETE`
- apply the change request `Allow underscores when explicitly configured` and
  confirm stable IDs, a new plan version only when needed, scoped code and test
  changes, refreshed evidence, and a new local feature commit
- inject one controlled validation, test, bad-test, design, spec-gap, or
  environment failure at a time; prove known mechanical causes bypass
  troubleshooting, an ambiguous cause enters `troubleshoot` once and returns
  its diagnosis to classification, incomplete diagnosis and budgets stop, and
  a localized post-wave repair re-enters through corrective plan/waves before
  the original oracle and invalidated gates rerun
- add `Pause after the current feature. Do not create a PR.` to the bound
  prompt, repeat `run`, and confirm `sdlc-start` and Stop continuation honor it
- edit the same prompt with requirements, design, and docs changes in turn,
  then confirm `sdlc-auto-steering` records each immutable revision and routes product-truth
  changes to the owning skill before implementation treats them as true
- confirm clearing steering allows resume
- run GUI or TUI checks only against harmless local disposable targets; both
  are required when claiming the exact 20-skill verification matrix passed

The full workflow test must not call `create-pr`, `review-pr`, or
`sdlc-merge-pr` against a real remote. Merge remains outside verification
unless the user explicitly requests a separate merge exercise.

### Real three-tier application test

The real-life profile is opt-in and separate from the preceding lightweight
fixture:

```text
$sdlc-workflow-test --create
$sdlc-workflow-test --create --keep
$sdlc-workflow-test --resume
$sdlc-workflow-test --destroy
```

`--keep` is valid only with `--create`; create, resume, and destroy are mutually
exclusive. `--resume` revalidates and continues the one exactly owned retained
failed or partial run; it does not create or replace a lifecycle. There is one
active three-tier application per verification root.
Every create first destroys the previous active exactly owned environment and
then creates a fresh lifecycle. If project safety, resource ownership, or
cleanup cannot be proven, replacement stops without starting another stack.
Cleanup covers both recorded IDs and resources discovered through the exact
verification-ID and Compose-project labels, including an interrupted run that
created Docker resources before persisting its inventory.
Every subsequent private lifecycle mutation and every Docker Compose action
must carry the immutable verification ID returned by prepare. The helper holds
the lifecycle lock through each Compose action, fixes the Compose project and
project directory, and rejects stale generations before mutation.
Repeated standalone destroy returns `ALREADY_DESTROYED`.

Create first runs the unchanged deterministic preflight. It then prepares one
owned project and uses only `$sdlc-start workspace init <project-folder>` and
`$sdlc-start run <prompt-path-or-unique-filename>` to build a task-board
application through the normal phase skills. The logical tiers are a browser
GUI, Django/Gunicorn web/API server, and PostgreSQL database. Exactly two Docker
Compose containers run the web and database services. Docker assigns the web
host port and binds it to loopback only; PostgreSQL is never host-published.
Before lifecycle creation, computer-use preflight must successfully capture a
real selected-browser state; tool discovery or process presence is insufficient.
That capture proves capability discovery only. Immediately before the first GUI
navigation in evaluation and again before UAT, the verifier repeats a fresh
capture for the exact selected browser while the host is unlocked and a normal
browser window is visible, unminimized, foreground, and on the current macOS
Space. A locked host is allowed only when the current Codex surface explicitly
confirms locked Computer Use is enabled for that session.
After workspace initialization, a deterministic renderer preserves the starter
identity and replaces its body before prompt intake. Fixed public base-image
pulls use an owned empty Docker CLI config only for those bounded pulls, never
for Compose. Runtime recording requires explicit web/database container roles
and verifies their canonical Compose service labels.

PASS requires semantic evidence for every phase, the clean promoted Git SHA,
unit/API/database/migration/vertical/GUI tests, all three layers, local
deployment, and computer-use GUI UAT. A just-in-time visibility failure is an
`ENVIRONMENT_DEFECT` at `pre-navigation-window-capture`, with no navigation or
GUI action claimed. A hung/timed-out call or response loss across browsers
stops all further Computer Use calls; fresh-session or service recovery is a
separate explicitly authorized action. The UAT must refresh accessibility state
after every successful action; reject blank input; create, refresh, complete,
and filter a unique task; correlate the same record through GUI, API, and
PostgreSQL; restart services
without deleting the volume; and prove persistence. The verifier launches one
fresh Google Chrome process group with an isolated verifier-owned profile and
verification-ID marker; existing Chrome instances are never selected or
closed. Computer Use must expose that marker before every action. Browser or
Playwright evidence cannot substitute for a
required computer-use harness, and screenshots alone cannot establish PASS.

Lifecycle state uses `agentic-sdlc/three-tier-lifecycle-v3`; semantic evidence
uses `agentic-sdlc/three-tier-results-v2`. Reports include structured Computer
Use readiness attempts, an explicit 20-skill evidence matrix, logical/container
layer inventory, tool/browser versions, project/report paths, resolved web/API/
health endpoints, internal database endpoint, baseline/promoted SHAs, phase and
test outcomes, exact resource IDs, UAT, and cleanup/retention status. Reports
omit prompts, raw logs, credentials, database contents, and screenshot content.

Default create closes only its exact verifier-owned Chrome process group and, after success or
failure, removes its exact project, private state/raw evidence, containers,
network, database volume, and built web image. Chrome identity ambiguity fails
closed before Docker deletion and retains resumable `CLEANUP_FAILED` state.
Create plus keep retains those resources and the dedicated Chrome instance.
Standalone destroy validates the root, lifecycle, exact browser process/profile
identity, paths, verification ID, Compose project, and both labels on every
present Docker alias. It canonicalizes and deduplicates resource identities,
preserves cumulative retry results, and never removes Docker, shared/base
images, unrelated resources, or an existing browser instance.

### Report interpretation

The report status means:

- `PASS`: all required deterministic capabilities, the seven live lanes, and
  the exact 20-skill matrix passed. GUI and TUI evidence is required for an
  all-skill PASS; unavailable harnesses keep the result PARTIAL rather than
  creating synthetic `NOT APPLICABLE` success.
- `PARTIAL`: no required check failed, but optional hook registration or one or
  more live lanes are missing or partial.
- `FAIL`: any required deterministic check, configured hook safety/parity
  check, supplied live lane, or live-evidence integrity check failed.

For the opt-in three-tier profile, missing Docker/browser/computer-use capacity
before a live attempt is PARTIAL, while any attempted required assertion
failure is FAIL. Three-tier PASS additionally requires semantic results and a
final lifecycle of `KEPT` by explicit request or fully `DESTROYED`.

`PARTIAL` is not a production-readiness result. It means the workflow is safe
enough to continue disposable verification, not that it is proven for real
project use.

### Fix and rerun policy

When verification finds a gap, fix the source of truth at the narrowest
responsible layer:

- update this design document when the contract is unclear, incomplete, or
  contradictory
- update the relevant `sdlc-*` skill when the phase contract is wrong
- update `sdlc-start/assets/hooks/` when the guardrail source is wrong
- sync installed hooks only through the approved hook installer flow after
  source fixes are reviewed
- rerun `$sdlc-workflow-test` after every fix

Do not update the report to hide a real failure. The report is evidence, not
the contract.

## Skill Responsibilities

### `sdlc-create-requirements`

Converts user intent, tickets, issues, Slack threads, Confluence pages, GitHub
context, or approved change requests into durable requirements in
`docs/requirements.md`. It preserves stable `REQ-*` IDs, records acceptance
and negative criteria, records optional Live Experiment Environment details for
safe later evaluation, and marks unclear items as open questions instead of
guessing.

### `sdlc-start`

Owns exactly `$sdlc-start workspace init [project-folder]` and the prompt-bound
`$sdlc-start run` action. Initialization preserves prompt history and
creates one starter only when empty. `run` binds one managed prompt to one
active run, snapshots each changed digest once, returns `ALREADY_COMPLETE` for
an unchanged completed prompt, and routes changed active revisions to
`sdlc-auto-steering`. It then coordinates the active SDLC run and reads
`docs/requirements.md`, `docs/design.md`, the project-instruction decision,
`active-run.json`, `current-state.json`, feature queue, fingerprints, latest
checkpoint, `STEERING.md`,
`steering/auto-steering.json`, and evidence. It selects the highest-priority
incomplete feature whose dependencies are satisfied, routes stale or unresolved
steering to `sdlc-auto-steering`, writes a checkpoint, and returns exactly one
next recommended skill. At the beginning of a run, it encourages the user to
provide a non-production or disposable live experiment environment and routes
any provided details through `sdlc-create-requirements`. It keeps one active
feature, routes `plan_locked` to `sdlc-prepare-execution`, routes all post-prepare
feature phases through the integration checkout, and routes UAT only after
exact promotion succeeds.

### `sdlc-gather-context`

Builds a compact context pack for one `REQ-*` or `FEAT-*`. It gathers official
vendor facts first, internal/project facts second, and code/test evidence
third. For serial multi-layer features, it also records layer owners, API or
command contracts, persistence expectations, fixtures, integration seams, and
gaps that affect the vertical slice. The context pack records sources,
constraints, unresolved risks, and design implications without dumping raw
pages, threads, or logs.

### `sdlc-create-design`

Converts requirements and gathered context into `docs/design.md`. It maps
stable `REQ-*` blocks to stable `FEAT-*` blocks, defines architecture,
boundaries, vertical end-to-end feature flow, layer map, data flow, control
flow, state, error handling, security, observability, validation, tests,
evaluation, rollback, and done criteria.

Failure-driven redesign has an additional admission gate. The accepted
requirements and evaluator/environment must be valid, the failure must
reproduce at the recorded integration commit, and `proven` or
`high_confidence` evidence must name the violated design decision, boundary, or
invariant. It must also show why a localized implementation, test, evaluator,
environment, or plan repair cannot satisfy the requirement and identify the
affected `FEAT-*` closure, invalidations, estimated work, and rollback path.
Only architecture topology, component/service responsibility, public
API/CLI/config/integration contracts, data ownership/lifecycle, migration,
security/trust boundaries, or cross-component workflow changes qualify.
Difficult work inside one existing private boundary does not.

Internal reconsideration may proceed automatically only when requirements,
public contracts, data lifecycle, security, permissions, deployment scope, and
external behavior remain unchanged. Broader redesign requires durable human
approval. Reaffirming the design returns evidence to classification without a
new fingerprint or loop; an admitted design change preserves stable feature
IDs, records the decision/change log and new fingerprint, and requires plan
vN+1.

### `project-agent-instructions`

Runs after validated requirements and design and before auto-steering or
planning. It inspects the exact selected project, inherited instruction chain,
active project instruction file, and relevant repository evidence. A new file
is justified only by durable, project-specific, actionable rules that change
agent behavior beyond inherited guidance.

The deterministic helper records a private manifest, decision, and state.
It creates `AGENTS.md` exclusively, refreshes it only when the provenance
marker and body digest prove the generated file is unchanged, and explicitly
reads the active file after a write. Human-owned files, same-directory
`AGENTS.override.md`, and configured fallback files are never overwritten.
Conflicts, material gaps, unsafe targets, stale provenance, or concurrent
changes block the workflow instead of silently weakening instructions.

### `sdlc-auto-steering`

Refreshes private runtime steering for the active run. It records every accepted
same-prompt revision in `STEERING.md`, linked by prompt ID, revision, digest,
and snapshot, with only a compact redacted summary and never raw prompt text,
keeps `steering/auto-steering.json` as the machine-readable disposition state,
and derives compact reminders from requirements, design, project instructions,
context, locked plan, fingerprints, steering, and recent evidence. It
classifies entries with exact disposition values such as `runtime-only`,
`requirements-change`, `design-change`,
`project-agent-instructions-change`, `docs-update`, `resolved`, `superseded`,
`rejected`, or `needs-human`. It does not choose the next phase directly and
does not edit committed project-truth docs.

### `sdlc-create-plan`

Creates a private, locked execution plan for exactly one ready feature. Plans
live under the local run directory, not in the repository. Existing locked
plans are never edited in place; changed design or context creates a new plan
version. For serial multi-layer features, the plan preserves the end-to-end
slice and expresses implementation as stable `TASK-*` records. Dependencies,
write claims, and conflict domains define which tasks can safely share a wave.
After completed waves, a proven localized defect creates only adjacent,
immutable corrective plan vN+1. It preserves every existing task definition
and completed-task digest, binds the exact diagnosis and original regression
oracle, and appends contiguous corrective tasks and dependency waves. If that
history cannot be preserved, the workflow stops for human direction.

### `sdlc-prepare-execution`

Runs after plan lock and before TDD. It requires a clean project checkout. A
named non-default branch is reused; a checkout on the verified symbolic
`origin` default is switched to a deterministic `feature/sdlc-*` promotion
branch. It validates the locked task graph and prepares or resumes the feature
integration branch/worktree at the exact project base SHA. The exact folder
selected by `workspace init` is enforced as the claim, worker-cwd, and
changed-path boundary even in a monorepo. It records schema-v5 private
execution state, supports confirmed interrupted-worker transfer and
resource-free future-wave replanning, and acquires an `agentic-sdlc` v3 lease
when nested in a managed outer worktree. It never implements behavior,
promotes, or force-cleans resources.
`replan-future` serializes the transition, compares full canonical definitions
and definition digests for every resource-owning wave, and replaces only
resource-free planned future waves.

### `sdlc-tdd`

Defines success before implementation. It writes or maps tests from
requirements, feature design, the locked plan, and any planned end-to-end slice,
inside the registered integration worktree, then records expected red or
already-green evidence. When a slice is present,
tests cover the smallest useful layer contracts or cross-layer validation
target that would fail if planned behavior is missing. It does not implement
production behavior or weaken acceptance criteria.

### `sdlc-implement-plan`

Coordinates the current feature's dependency waves. It seals the TDD base,
creates immutable task assignments, and dispatches one fresh task agent per
safe task with its own branch and private worktree. Native agents are preferred;
when unavailable, one fresh sequential `codex exec` process runs with exact
scope cwd, `workspace-write`, `--ephemeral`, stdin assignment, and structured
output. Each task validates and reviews inside declared ownership. The
coordinator screens staged content and evidence for obvious secrets/private
endpoints, then produces one direct-child commit with normal Git hooks. The
coordinator verifies results, merges in stable order with explicit merge
commits, runs combined evidence, and performs non-force worker cleanup before
advancing. A corrective assignment carries its exact diagnosis and original
regression oracle. After the bounded repair it runs that oracle first, then the
counterfactual or affected-boundary check, normal validation and
unit/integration tests, and every failed or invalidated evaluation criterion at
the new integration commit. Plan or design defects return through
classification instead of widening scope.

### `sdlc-validate-codes`

Checks whether the implementation can build, parse, lint, type-check, import,
validate configuration, and stay inside the locked plan and End-To-End Slice.
After those mechanical and boundary checks pass, it uses `code-review` in
review-only mode against the current feature diff, changed files, tests, locked
plan, and design context. It records validation and review evidence in the
local run state and classifies failures instead of treating missing tooling,
broken tooling, slice drift, or blocking review findings as success. This is
not PR readiness; `review-pr` remains the PR review and merge-readiness phase
after PR creation.

### `sdlc-unit-tests`

Runs behavior tests for the current feature, including unit, integration,
component, contract, regression, and mock-based tests when applicable. When the
locked plan defines an end-to-end slice, it records the tests that prove the
slice's cross-layer validation target or classifies the gap. It records test
evidence and classifies failures as implementation, test, environment, or
design defects.

### `sdlc-evaluate`

Observes product behavior against acceptance and negative criteria after
validation and tests pass. It chooses the correct route for the product shape:
`sdlc-gui-test`, `sdlc-tui-test`, API/service checks, performance checks, or
manual review. When a locked plan defines an end-to-end slice, it observes the
feature through that user-visible or system-visible flow; layer-isolated checks
alone are insufficient unless the plan says no slice applies. When
`docs/requirements.md` provides a confirmed safe Live Experiment Environment,
it may use that environment within the recorded allowed actions; missing,
unsafe, or unconfirmed live access is classified instead of guessed around.
Passing tests alone are not treated as evaluation.

On failure, evaluation emits `failure-event-v1` with the criterion/test ID,
expected and observed behavior, reproduction oracle, evidence digests, exact
integration commit, environment identity, and requirements/design/plan
fingerprints. It distinguishes an already-proven mechanical owner from a
diagnosis-required symptom; it does not speculate about implementation or
design.

Observability is eligible only for a predefined runtime operational criterion
such as release/canary behavior, performance, reliability, resilience,
capacity, or efficiency. Before Grafana readiness, one criterion must define
one exact measurement and Grafana-backed signal with non-Grafana provenance,
explicit authority, candidate and baseline/control attribution and absolute
windows, required coverage, and exact pass, fail, and inconclusive conditions.
Each provider call admits at most one grade-changing query; another query
requires an updated criterion ledger, and query budgets are ceilings rather
than batch targets. Missing signal provenance or criterion fit makes a required
criterion inconclusive with `SPEC_GAP` and causes zero Grafana calls.
Passive read-only production telemetry may pass or fail only that operational
criterion when those requirements are complete; otherwise the result is
inconclusive. It never authorizes a production workload or substitutes for
functional, semantic, end-to-end, or agent-trace evaluation. Offline skill
regression evals use frozen or mocked sanitized telemetry.

### `sdlc-update-documents`

Updates project-facing documentation after feature evaluation, resolved
documentation steering, UAT, or final run evidence. It may update README,
changelog, usage docs, examples, docs indexes, or generated docs that describe
implemented behavior. When documentation describes a multi-layer feature flow,
it uses evaluated end-to-end slice evidence rather than implementation intent
alone. It records documentation evidence under the private run directory as
`evidence/FEAT-*/documents.md` or `evidence/uat/documents.md`. It does not edit
`docs/requirements.md` or `docs/design.md`; spec, design, or missing slice
evidence routes back to the responsible phase.

### `sdlc-gui-test`

Controls and observes browser UI behavior when the evaluation plan requires
GUI evidence. It uses Computer Use when desktop/browser state is part of the
acceptance contract, otherwise Browser or Playwright when suitable, captures
screenshots or accessibility snapshots, and records pass/fail results against
acceptance criteria.

### `sdlc-tui-test`

Controls terminal, CLI wizard, or TUI behavior with transcript and exit-code
evidence. It validates prompts, inputs, outputs, side effects, and acceptance
criteria without storing credentials in transcripts.

### `sdlc-classify-failure`

Classifies failed phases before the loop retries. The deterministic helper
validates and deduplicates `failure-event-v1`, optional `diagnosis-v1`, and the
stable blocker identity; owns taxonomy routing, design admission, budget
enforcement, invalidations, and crash-safe intent/commit transitions; and
derives the human-readable failure log.

Proven test, local implementation, specification, evaluator, environment,
policy, or human causes bypass troubleshooting. An ambiguous or persistent
failure routes to `troubleshoot` exactly as a conditional diagnostic branch.
The diagnosis result returns here and is either routed to its proven owner or
stopped as missing evidence/unresolved. Failure to find an implementation bug
never proves design. Probable or incomplete diagnoses cannot authorize repair
or redesign.

### `sdlc-align-specs`

Checks that requirements, design, locked plans, tests, implementation,
documentation, end-to-end slice evidence, and other evidence tell one
consistent story. For vertical features, it verifies that the design's feature
flow and layer map, the locked plan's End-To-End Slice, tests, implementation
boundaries, validation evidence, evaluation evidence, documentation, and UAT
all describe the same slice or explicitly record why no slice applies. It is
SDLC-specific and does not replace the general `align` skill. Drift is routed
to the responsible SDLC skill.

### `sdlc-commit`

Seals at most one final integration commit after validation, tests, evaluation,
documentation, and alignment pass. It then verifies the original project
checkout is still clean at the recorded base, fast-forwards it to the exact
sealed integration SHA, and non-force-cleans the integration worktree/branch.
It records worker, merge, final, promoted, and cleanup evidence and never pushes.

### `sdlc-uat-tests`

Runs product-level UAT after all feature commits are complete. It builds a UAT
matrix from requirements, validates cross-feature user journeys and negative
criteria, may use the confirmed safe Live Experiment Environment within its
recorded allowed operations and reset rules, records evidence, and marks the
product ready for `create-pr` only on pass. For a managed outer worktree, final
alignment, UAT, and documentation evidence plus a clean exact promoted head and
zero internal resources release the Agentic SDLC lease before PR publication.

### `create-pr`

Reuses the existing PR creation skill as the SDLC PR handoff. In an SDLC run,
it uses publication-only mode after UAT passes and the Agentic SDLC lease
releases. Current clean `HEAD`, commit/execution evidence, the exact promoted
SHA, and any existing remote PR head must agree. It does not stage, edit,
commit, merge the base, resolve conflicts, repair checks, switch branches, or
publish another SHA. Any required branch change routes through
`sdlc-classify-failure` and `sdlc-start`, reruns every invalidated gate, and
produces a newly promoted exact SHA before publication is retried. It records PR
evidence when local run state is available.

### `review-pr`

Reuses the existing PR review skill for merge-readiness review. For SDLC PRs,
it uses findings-and-readiness-only mode. It requires the PR head to equal the
clean exact promoted SHA and checks requirements, design, local validation,
tests, evaluation, UAT, commit evidence, correctness, regressions, missing
tests, checks, reviews, and conflicts without checking out, editing, committing,
merging, rebasing, updating, resolving, or pushing the branch. Findings that
require a head change return through `sdlc-classify-failure` and the coordinator
before review resumes.

### `sdlc-merge-pr`

Merges only after an explicit user request for a specific PR. It verifies PR
status, checks, reviews, branch state, and UAT evidence. The current remote PR
head, clean local HEAD, promoted SHA, and review evidence must all identify one
commit. It writes short-lived authorization for one exact
`gh pr merge <pr-number-or-url> [--merge|--rebase|--squash]
--match-head-commit <promoted-sha>` command immediately before the guarded
action; the merge-queue form omits the strategy. Implicit PR selection,
administrator bypass, branch deletion, repository override, other flags, shell
operators, and appended actions fail closed. The skill records merge evidence
and marks the run complete when applicable. Drift returns through failure
classification; active Agentic SDLC does not use a race-prone GitHub merge MCP
path.

## Failure And Retry Model

The loop does not retry blindly. A failing validation, test, evaluation, UAT,
policy, or PR step is routed through `sdlc-classify-failure` so the state
records the immutable failure, stable blocker, responsible phase, repair
history, invalidations, budgets, next skill, and any human blocker.

The normalized records are:

- `failure-event-v1`: the exact failed criterion, expected/observed behavior,
  reproduction, evidence digests, integration commit, environment identity,
  and requirements/design/plan fingerprints.
- `diagnosis-v1`: causal proof, confidence, remedy scale, regression oracle,
  evidence references, and proposed classification.
- `repair-control-v1`: the atomic active-blocker projection with route history,
  attempts, active time, feature dispatches, invalidations, and status.
- `repair-journal`: append-only intent/commit transitions used for crash-safe
  replay.

`event_id` is the hash of exact commit-bound content and evidence.
`blocker_key` is the stable
`component | operation | error class | source boundary` identity and excludes
timestamps, temporary IDs, line numbers, checkpoints, and commits.

If a responsible cause is already proven, classification routes directly to
the owner. Otherwise, ambiguous, persistent, intermittent, or cross-boundary
failure enters Agentic SDLC diagnostic mode in `troubleshoot`. That mode
preserves the exact failed commit and criteria, builds the failure contract and
causal model, finds the earliest divergence, and returns `diagnosis-v1`. It may
use explicitly scoped, reversible, uncommitted instrumentation only in the
private integration worktree and must remove it before handoff. It cannot
commit a product repair, change tests/specifications, call design, or select a
repair phase.

Typical routes are:

- spec gaps -> `sdlc-create-requirements`
- missing or conflicting context -> `sdlc-gather-context`
- design defects -> `sdlc-create-design`
- plan defects -> `sdlc-create-plan`
- preparation or scheduler defects -> `sdlc-prepare-execution` or
  `sdlc-create-plan`
- worktree identity drift -> the owning execution phase with resources retained
- undeclared task ownership or changed plan digest -> `REPLAN_REQUIRED`
- wrong or missing tests -> `sdlc-tdd`
- proven implementation defect in an active task -> that task's existing
  attempt contract in `sdlc-implement-plan`
- proven implementation defect after waves complete -> immutable corrective
  plan vN+1 and appended waves before `sdlc-implement-plan`
- ambiguous evaluation failure -> `troubleshoot`, then back to
  `sdlc-classify-failure`
- missing decisive evidence or competing hypotheses -> stop as
  `BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED`, never design
- proven system-contract defect -> the design admission gate, then
  `sdlc-create-design`
- ordered merge conflicts -> `sdlc-implement-plan` without history rewrite
- unsafe worker/integration cleanup -> `CLEANUP_BLOCKED` without force removal
- moved project/integration state -> `PROMOTION_BLOCKED`
- any execution coordinator schema v1/v2/v3 record ->
  `WORKFLOW_UPGRADE_REQUIRED`, including completed records
- validation defects -> `sdlc-validate-codes` after repair
- behavior or acceptance defects -> `sdlc-evaluate` or the correct evaluator
- documentation drift -> `sdlc-update-documents`, then `sdlc-align-specs`
- local/remote/promoted/reviewed PR head drift -> `PR_HEAD_DRIFT` and human
  ownership input without push, overwrite, or merge
- environment defects -> stop or request the minimum missing setup
- policy blocks -> stop unless the required explicit authorization exists

Implementation or test changes invalidate validation, tests, evaluation,
documentation/alignment, and commit evidence at the old commit. Plan changes
invalidate execution preparation and downstream evidence; design changes
invalidate plan and downstream evidence for the affected feature closure;
requirements changes invalidate design and all downstream evidence.
Environment-only correction reruns the failed gate at the same commit.
Acceptance criteria cannot be weakened to make a repair pass.

A successful remediation with invalidations enters
`revalidation_required`, not terminal `resolved`. The authoritative
`revalidation-cursor-v1` orders each invalidated surface and exact next skill.
Every gate advances it with immutable `revalidation-evidence-v1` whose source
is a structured phase-owned `passed` result. Its source digest, current
requirements/design/plan fingerprints, immutable classification, successful
dispatch, and cursor identity are bound to the active repair. Pre-commit gates
must match the live integration HEAD. The final commit gate is recorded only
after promotion and non-force cleanup complete; it must match the
coordinator's promoted SHA, the clean project checkout HEAD, and verified
presence on the recorded base branch plus absence of the integration worktree
and branch. The Stop hook rejects UAT, PR, and
publication routing until the cursor has cleared every invalidation, including
fresh commit evidence.

One stable blocker tranche permits at most two localized remediations, one
design-scale remediation, three total remediation attempts, and 60 active
minutes across diagnosis, repair, verification, and reporting. A feature
permits at most four repair dispatches across causally independent blockers.
Diagnostic experiments consume active time but not remediation attempts; three
low-information experiments require a rebuilt model, and diagnosis stops when
no new decision-changing experiment exists. Before every remediation retry,
new evidence and a genuinely new falsifiable hypothesis are mandatory. A first
direct repair that fails with the same blocker requires troubleshooting before
attempt two. The same blocker after design remediation, semantic A-to-B-to-A
cycling, or phase churn without new evidence stops immediately.

Exact duplicate events, classification replays, permission denials, and crash
recovery consume no attempt. A causally independent blocker receives a fresh
blocker budget but continues to consume the feature total. Only a new explicit
user instruction authorizes another tranche; counters never reset silently.

No sealed, promoted, or completed execution is reopened. A post-promotion UAT
defect starts a corrective run from the exact promoted commit. Every successful
repair reruns the original oracle, the counterfactual or affected-boundary
check, validation and unit/integration tests at the new integration commit, and
every failed or invalidated evaluation criterion before documentation,
alignment, or commit may continue.

## Guarded Git Actions

Git actions are intentionally split:

- Task agents create one scoped worker commit through the private transition
  helper; the coordinator creates ordered merge commits.
- `sdlc-commit` seals final integration changes, performs exact ff-only local
  promotion, and never pushes.
- In active Agentic SDLC mode, `create-pr` only publishes the clean exact
  promoted SHA after UAT passes; it never changes that SHA.
- In active Agentic SDLC mode, `review-pr` is findings-and-readiness-only and
  never changes the PR head.
- `sdlc-merge-pr` merges only with explicit user instruction and only while the
  remote head still equals the exact promoted and reviewed SHA.

The optional PreToolUse hook can require short-lived local authorization files
under the active run's `permissions/` directory:

- `commit-authorization.json`
- `execution/<action-id>.json`
- `pr-authorization.json`
- `merge-authorization.json`

These files are written immediately before the guarded action, scoped to the
branch or PR, include an expiry, and are removed or allowed to expire after the
attempt. PR authorization additionally binds `phase: "create-pr"`, the exact
branch, `expected_head` equal to the clean promoted SHA, and
`uat_status: "passed"`; it guards active-run Git pushes, `gh pr create`, and
GitHub PR-creation MCP calls without blocking GitHub PR reads. The guarded push
allows exactly `origin` plus one `HEAD:<authorized-branch>` refspec, and CLI/MCP
PR creation must name that same head branch. Sensitive shell actions use one
direct executable action; executable wrappers, absolute executable paths,
prepended commands, shell operators, redirections, and appended actions fail
closed. Raw execution authorization also binds action, canonical worktree, Git
common directory, branch, expected HEAD, and exact target/command. Normal
execution-plane mutations use the private helper, which revalidates the same
identity before and after each transition.
Merge authorization binds `phase: "sdlc-merge-pr"`, the exact branch, PR,
promoted/reviewed head, explicit user request, passing checks/review/UAT,
expiry, and the one canonical, explicit-PR, single-action head-matched CLI
command. Only the optional long-form merge strategy is variable.

## Resume And Idempotency

The workflow is designed to survive conversation loss and repeated resumes.
Stop continuation repeats `$sdlc-start run <bound-unique-filename>` and never
requires a prompt ID or run ID. `sdlc-start` accepts the immutable prompt
revision, then reads the latest checkpoint and current state before selecting a
phase. If nothing changed, it returns the same feature and next recommended
skill without duplicating history. If partial writes occur, it repairs state
from the newest complete checkpoint that matches evidence.

Stable IDs are part of the design:

- `REQ-*` IDs are stable and are not renumbered.
- `FEAT-*` IDs are stable and are not renumbered.
- Locked plan files are immutable; new information creates a new plan version.
- Evidence is refreshed or appended rather than silently erased.
- Task assignments and results are immutable digest-bound records; one file per
  task prevents parallel agents from sharing mutable JSON.
- Intent is journaled before Git mutation, and recovery re-observes refs,
  worktree registration, branch, SHA, cleanliness, and ancestry before deciding
  whether to resume, retain, or block.
- Worker and merge commits are retained. No rebase, squash, amend, reset,
  cherry-pick, or force cleanup is used by the execution plane.
- Every execution coordinator schema v1, v2, or v3 record fails closed with
  `WORKFLOW_UPGRADE_REQUIRED` without mutation, including completed records.
- An unfinished pre-prompt run also fails with `WORKFLOW_UPGRADE_REQUIRED`;
  completed unbound history remains readable without an adoption shim.

## Boundaries

The workflow intentionally does not:

- create a public phase-orchestration workflow CLI; the private prompt helper
  performs only deterministic workspace, snapshot, intake, and disposition
  transitions
- let hooks choose phases or implement workflow logic
- treat literal command classification as a shell sandbox or construct guarded
  Git/GitHub actions indirectly through variables, substitutions, interpreters,
  or generated programs
- treat worktree separation as an operating-system security boundary
- run two parallel tasks with overlapping write claims or conflict domains
- rewrite worker/merge history or force-clean unverified Git resources
- use conversation memory as authoritative run state
- commit local SDLC run state, screenshots, transcripts, or plans
- merge without explicit user instruction
- bypass validation, tests, evaluation, UAT, PR review, or failure
  classification

This keeps the SDLC loop agentic while preserving recoverability, auditability,
and a clear separation between product truth, private execution state, external
capabilities, and runtime guardrails.
