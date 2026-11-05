---
name: sdlc-merge-pr
description: "Use only as part of the Agentic SDLC workflow; complete an opted-in delivery slice through protected direct merge and exact-result verification after independent qualification, review and policy admission."
user-invocable: false
---

# Merge PR

## Help

For `$sdlc-merge-pr --help` or `$sdlc-merge-pr -h` (including native Claude forms), return concise help and stop before
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

Use `$sdlc-merge-pr` in Codex, `/sdlc-merge-pr` in Claude Code, or
`/skills:sdlc-merge-pr` in the Claude plugin. Dollar-prefixed skill examples
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

Complete a delivery slice through its own protected merge adapter after accepted
run-level opt-in. Default runs stop at a reviewed PR. A provided cluster alone
never grants merge authority.

## When To Use

- The bound root selected `merge-verified` for this repository, default branch,
  delivery slice and live/merge effects in the accepted objective revision.
- `review-pr` passed for the exact promoted candidate and owner-produced
  qualification covers every mandatory slice and inherited criterion.
- A submitted or merged candidate needs read-only reconciliation after a pause.

## When Not To Use

- Do not invoke ordinary `merge-pr` orchestration from SDLC.
- Do not merge a reviewed-PR-only run, unsupported queue or managed outer child.
- Do not treat COMMENT review attestations as GitHub approvals.

## Required Reads

- `../worktree/references/document-delivery.md` and
  `../worktree/references/workflow-contract.md`.
- `../merge-pr/references/actions-protocol.md` for the shared protected broker.
- Accepted completion policy, immutable execution basis, promoted candidate,
  qualification producer receipt and exact current `review-pr` evidence.
- Current PR/base, repository rules, checks, conversations and human objections.

## Inputs

- Verified coordinator identity, active run and frozen delivery-slice identity.
- Accepted objective revision and its scoped completion policy.
- Promoted candidate, owner-produced qualification receipt and exact PR review.
- Existing submission receipt when reconciling an attempted or completed merge.

## Must Not

- Do not infer merge authority from a supplied cluster or passing test labels.
- Do not bypass the protected broker, repository rules or required approvals.
- Do not reuse stale qualification or repeat an ambiguous merge submission.
- Do not dispatch dependent delivery until the merged result is verified.

## Process

1. Verify native coordinator ownership, active slice and current accepted intent.
   Keep the user's run-level opt-in scoped to repository, default branch, slice,
   objective revision, live operations and merge-triggered effects. Do not ask
   again for an already-authorized slice. Policy changes require a new revision.
2. After `review-pr`, use private `workflow_intake.py --owner agentic-sdlc
   attest-delivery --run-dir … --execution SLICE-001 --candidate …`. The candidate
   contains the exact `skills-review/v2` report and qualification reference.
   This adapter publishes a COMMENT attestation and retains its real review ID.
3. Use private `submit-delivery --run-dir … --execution SLICE-001`. The adapter
   independently rechecks current source/basis, promoted/tested/reviewed artifact,
   base, CI, operator authority, objections and conversations. Every mandatory
   criterion must pass with clean trial/reset provenance and independent target
   observation. Live N/A is restricted to reviewed prose-only changes.
4. Require compatible protected workflows already installed on the default
   branch. Queue rules fail closed. Never install workflows, weaken protection,
   substitute credentials, push default, or directly approve/merge through CLI,
   REST, GraphQL or MCP. The broker alone owns protected approval and merge.
5. Persist submission intent before dispatch. The returned broker run ID and
   immutable intent bind exact PR/head/base/review/method. If submission is
   ambiguous, use `reconcile-delivery`; do not repeat dispatch or infer success.
6. Reconcile actual merge and then exact-result CI/publication. A merged candidate
   whose verification fails remains `merged-verification-failed`; pause dependent
   delivery and resume verification without merging again. Head/base drift before
   merge returns to corrective integration and fresh qualification.
7. After `result-verified`, record slice progress. The next branch starts at the
   current default branch through the shared source helper. Phase/feature
   completion still requires all its source acceptance obligations. Return to
   `sdlc-start`; complete the run only when its full accepted program is proven.

## Idempotency

- An existing submission is reconciliation-only; never repeat an uncertain effect.
- Preserve failed/intervened trials and old objective evidence.
- Unsupported old active state is retained for explicit recovery, never converted.

## Failure Handling

- Missing authority, workflow compatibility or operator configuration blocks.
- Missing, stale, forged, skipped or intervened evidence blocks admission.
- Base/head/artifact/configuration drift requires fresh qualification and review.
- Required GitHub approvals and branch rules remain enforced by the broker/API.
- Report submitted, merged, result-verified or merged-verification-failed precisely.

## Writes

- Owner-produced review, submission and completion records under private
  `delivery/<slice>/`; no caller-written passed labels authorize effects.
- Protected COMMENT attestation and broker dispatch, only within accepted intent.
- No direct branch deletion or protected-branch mutation.

## Completion Criteria

- Exact merged result and applicable result checks are independently verified, or
  a specific pending/failed state is preserved and reported.
- The root receives evidence references and the next eligible phase/slice.

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

- No skill-local references are required by default; use project files and current official documentation as needed.
