---
name: sdlc-uat-tests
description: "Use only as part of the Agentic SDLC workflow; run explicitly requested UAT at a meaningful ready objective or milestone scope; retain honest pending outcomes without blocking eligible implementation or delivery."
user-invocable: false
---

# UAT Tests

## Help

For `$sdlc-uat-tests --help` or `$sdlc-uat-tests -h` (including native Claude forms), return concise help and stop before
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

Before workflow reads, load `../global-context-management/references/agent-hosts.md`
for the selected agent home, native identity and required runtime setup.

Use `$sdlc-uat-tests` in Codex, `/sdlc-uat-tests` in Claude Code, or
`/skills:sdlc-uat-tests` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Coordinator Invocation Boundary

This skill requires verified workflow context from the active Agentic SDLC
coordinator. Before acting, verify the selected project, active run, phase and
coordinator ownership through the workflow's existing state helpers. Without
that evidence, return without mutation; a skill mention or automatic selection
is not workflow authorization. In Claude Code this skill is hidden from the
public command menu but remains available to the verified coordinator.

## Purpose

Load `../worktree/references/workflow-invariants.md` for the shared ownership and
evidence rules, then `../worktree/references/optional-uat.md` for UAT policy.

Run only requested UAT against a meaningful ready scope. No UAT is requested by
default. The default timing for a request is after implementation of the whole
accepted objective/release; an explicitly selected coherent milestone may run
sooner. Missing optional infrastructure does not block implementation or eligible
PR/merge. Automated evidence does not claim human business acceptance.

## When To Use

- The accepted objective explicitly requests UAT.
- Its capabilities and dependencies form the agreed testable scope.
- The selected project/runtime harness can execute authorized scenarios.

## When Not To Use

- Do not infer a UAT request from a provided environment or an implementation task.
- Do not run after every task, wave or slice by default.
- Do not use task tests as whole-product UAT or create/merge PRs here.

## Inputs

- The accepted UAT policy, candidate and explicitly requested objective/milestone.

## Required Reads

Read `../worktree/references/optional-uat.md` before scheduling. Read
`../worktree/references/document-delivery.md` for program/source coverage or
protected delivery-policy evaluation, and `../worktree/references/workflow-contract.md`
before execution reconciliation or lifecycle operations. Restore the accepted `uat` policy and exact execution basis. Read the
canonical requirements/design, accepted criteria, product startup instructions,
existing harnesses and authorized environment/reset procedures. Keep all secrets
in their intended protected runtime store, outside evidence and task state.

## Writes

Write the private UAT report, readiness declaration, actual producer receipts and
late-testing handoff. Include artifact/environment/trial identities only when
observed. Preserve every previous attempted trial and unresolved cleanup owner.

## Process

1. If UAT was not requested, record `not_requested` and return to eligible delivery.
2. Check software/dependency readiness for the accepted objective or explicit
   milestone before costly setup. An incomplete scope remains `pending`.
3. Use an existing confirmed nonproduction target directly through its project
   harness. Do not call the Nebius provisioning skill merely because an environment
   exists. Only explicitly selected `$test-software-in-container` or
   `$test-live-on-nebius` campaigns may use those owners. Nebius may create its
   target after software readiness; no pre-existing live target is necessary.
4. Check environment, access, data, scenarios and smoke readiness on the exact
   candidate. Record missing prerequisites, keep a late-test handoff and continue
   implementation/eligible publication. Do not spend repair attempts waiting for
   optional infrastructure or keep Stop in a retry loop. For unchanged missing
   prerequisites, reuse the pending handoff; retry setup/research only after a
   relevant input, availability or authority change is observed.
5. Run the frozen harness with private `qualify --execution OBJECTIVE`, using
   `--stage aggregate` for the final automatic aggregate. Preserve worker fences.
   The qualification plan, source/tree, artifacts, trial/reset observations and
   actual assertions must match. A changed candidate needs new evidence.
6. Web GUI scenarios use headless Playwright, an owned fresh context, repeatable
   assertions, actual browser version and independent API/database/service oracles
   where required. Screenshots alone are not PASS. Required native desktop
   behavior remains a separate explicitly selected contract.
7. Run exact owner cleanup on success and failure. The pinned cleanup action
   verifies owned resources absent and writers quiescent; borrowed environments
   remain intact. Preserve unproven cleanup and route recovery to that owner.
8. Report UAT independently of delivery admission. Only complete executed evidence
   is `passed`; pending/incomplete never become PASS or N/A. Later verification
   validates frozen evidence without requiring a cleaned environment to persist.

## Idempotency

Do not duplicate resources, reset budgets or overwrite failed trials. Each trial
has its own identity and repeatable data. After terminal completion, later user
testing starts a new campaign from the retained candidate handoff.

## Failure Handling

Missing optional prerequisites are `pending`, not defects. Actual assertion
failures retain their failure classification and existing bounded remediation;
block affected publication until repaired while independent implementation may
continue. Incomplete execution with proven cleanup may remain eligible; unresolved
writers/cleanup remain owner obligations. A narrower later PASS cannot clear a
larger failed scope. Never reinterpret interventions as clean product evidence.

## Must Not

Create PRs, merge, fabricate runtime evidence, ignore failed negative criteria,
mutate canonical specifications, use an unconfirmed/production target without
exact action authority, or exceed accepted operations. Do not launch either
explicit-only testing skill through implicit routing.

## Completion Criteria

- The report states `not_requested`, `pending`, `running`, `passed`, `failed` or
  `incomplete`, with exact scope/candidate and concise reasons.
- PASS has complete producer-owned assertions, observation and cleanup evidence.
- Missing prerequisites leave a durable handoff and allow eligible delivery.
- Publication uses separate implementation admission; required development checks,
  CI, review, authority and genuine correctness dependencies remain enforced.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- `maintain-project-specs` is the sole semantic, schema, and validation owner
  of both canonical specs. Inside Agentic SDLC, only its routed
  `sdlc-create-requirements` and `sdlc-create-design` authoring adapters may
  write their respective managed records; all other phase skills route changes
  through those adapters and return validation to the shared owner.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `<agent-home>/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Keep one delivery candidate active; eligible tasks may span features within its frozen slice.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Use `assets/templates/uat-report.md.template` when creating the corresponding artifact.
