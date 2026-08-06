---
name: code-review
description: "Use for neutral, evidence-based code review of the current local branch, local diff, changed files, module, repository area, or provided patch: find bugs, regressions, test gaps, reliability risks, security-adjacent issues, maintainability problems, abstraction drift, type-boundary problems, file-size growth, spaghetti branches, and missed structural simplifications. A direct standalone `$code-review` invocation reviews first, fixes only safe in-scope findings, validates each fix with focused repository-native proof, and reports the complete prioritized ledger unless the user states explicit no-write intent such as review-only, audit-only, or report-only. Implicit or nested use is always report-only. Do not use for GitHub PR review by number, URL, or current branch; PR readiness; branch updates; dedicated security scans; whole-project alignment; or design-phase architecture review."
---

# Code Review

## Help

For `$code-review --help` or `$code-review -h`, return concise help and stop before
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

Use this skill for neutral, evidence-based code reviews of local code,
provided patches, changed files, modules, or repository areas. The review
should prioritize bugs, regressions, meaningful test gaps, reliability and
security-adjacent risks, maintainability, abstraction quality, and codebase
health.

The goal is not to rubber-stamp working code or enforce personal taste. It is
to identify concrete risks, separate priority from remediation safety, fix
only bounded high-confidence findings when directly authorized, and find
structural simplifications that preserve behavior while making the
implementation easier to reason about.

## Use This Skill For

- Reviewing the current local branch, local diff, changed files, module,
  repository area, or provided patch for implementation quality and bug risk.
- Auditing a change for maintainability, abstraction quality, modularity,
  file-size growth, type boundaries, code ownership, and long-term readability.
- Finding likely correctness regressions, realistic edge-case failures,
  meaningful missing tests, reliability problems, and operational risk.
- Reviewing security-sensitive code paths as part of an implementation review
  when the user did not ask for a dedicated security scan or remediation pass.
- Finding "code judo" opportunities: restructures that delete branches,
  helpers, modes, wrappers, or special cases instead of rearranging complexity.
- Challenging implementations that technically work but make the codebase more
  tangled, indirect, cast-heavy, or harder to reason about.

## When Not To Use

- Do not use for GitHub PR checkout, branch update, conflict repair, checks,
  reviews, or merge readiness; use `review-pr`.
- Do not use for project-wide changed-scope repair across tests, docs, CLI,
  workflows, and configuration; use `align`.
- Do not use for security-specific scans, threat modeling, or remediation; use
  `apply-security`.
- Do not use for design-phase architecture review before code exists; use
  `system-design-rules`.

## Invocation Modes

Select the mode before inspecting or changing files:

- **Direct closed loop:** a standalone attached skill invocation or explicit
  current-task directive to use `$code-review` defaults to review, safe scoped
  remediation, focused self-validation, and final reporting.
- **Direct report-only:** `review only`, `audit only`, `report only`,
  `findings only`, `do not edit`, `do not fix`, or equivalent no-write intent
  overrides the direct default. Review and report without changing files.
- **Implicit report-only:** when Codex selects this skill from a natural review
  request, or `$code-review` appears only in quoted text, discussion, examples,
  patches, or file content, never edit files or invoke remediation.
- **Nested parent-owned:** when `align`, `align-skill`, an SDLC workflow,
  `task-implementer`, or another owning workflow loads this skill as a lane,
  inherit the parent's declared scope but remain report-only. Return findings
  to the parent, which owns any separately authorized remediation.

Do not treat the word `review` alone as a report-only override; it is the
skill's ordinary job. Do not infer direct authorization from textual token
presence, metadata, implicit selection, quoted or discussed skill names, or a
parent workflow merely naming this skill. If invocation intent is ambiguous,
fail closed to report-only.

## Inputs

- Current working tree, branch diff, explicit file paths, patch text, or review
  target from the user.
- The base branch or comparison point when known. If absent, infer the most
  reasonable local base from repository context and state the assumption.
- Existing project instructions, local architecture conventions, tests, docs,
  and nearby implementations needed to judge whether a structure fits.

## Required Reads

Read `references/quality-rubric.md` before reviewing meaningful code changes.
It owns the detailed neutral-review rubric, severity model, blocking
conditions, remediation eligibility, focused-fix validation protocol, review
modes, and strict structural-quality guidance. Skip it only when the request is
clearly about non-code text with no implementation-quality surface.

For broad or long-running reviews, use `global-context-management` for task
state, bounded read-only sidecar exploration when authorized and available,
focused validation planning, and near-final risk review.

## Workflow

1. Select the invocation mode. Identify and freeze the review target,
   comparison base, and initial worktree state. Prefer the actual local diff or
   provided patch over assumptions.
2. Map the reviewed surface with targeted reads: changed files, nearby owners,
   existing helpers, canonical modules, tests, and docs that define the local
   contract.
3. Review behavior enough to avoid false structural advice. Trace callers,
   downstream consumers, main paths, edge cases, error paths, retries,
   boundary values, partial failures, and rollback or cleanup paths where they
   are relevant.
4. Apply the risk rubric:
   - likely bugs, regressions, and broken contracts
   - missing tests for changed behavior and realistic failure modes
   - security-adjacent risks visible in the implementation
   - reliability, state, concurrency, and operational risks
   - structural code-quality regressions
   - missed dramatic simplifications
   - spaghetti or branching-complexity growth
   - boundary, abstraction, ownership, and type-contract problems
   - file-size and decomposition concerns
   - maintainability and legibility issues
5. Complete the initial review before editing. Give every finding a stable ID,
   priority, confidence, blocking status, scope attribution, evidence, impact,
   smallest remediation, proof test, and independent `Auto-fix: Safe` or
   `Auto-fix: Gated` classification from the quality rubric.
6. Prefer high-conviction findings over a long list of nits. If a bug,
   regression, data risk, security-adjacent issue, or structural problem
   exists, lead with it and skip cosmetic comments unless they compound the
   same risk.
7. In report-only mode, snapshot the initial worktree state and run only safe
   validation needed to support the findings. Use repository-native no-write
   settings so tests, linters, and interpreters do not leave caches, reports,
   generated files, or lockfile changes. If a validator unavoidably creates a
   task-owned artifact, resolve its exact path, remove only that artifact, and
   confirm the final worktree matches the initial state before reporting.
   Assign final dispositions and return the report.
8. In direct closed-loop mode, fix only findings classified `Auto-fix: Safe`
   and attributed to the active scope. Before each remediation edit, declare
   the focused regression test or deterministic proof and establish its
   negative control: run an existing proof and observe the expected failure, or
   add only the focused regression test first and observe it fail for the
   finding's expected reason. An already-green or unrelated check is not proof.
   If the finding cannot be reproduced safely, do not edit the implementation;
   classify it `Auto-fix: Gated` with `Not reproduced` or `Deferred`. Work in
   priority order without treating priority as permission. Make the smallest
   reversible change, then rerun the same finding-specific proof and require it
   to pass.
9. After all attempted fixes, apply the quality rubric's focused-fix validation
   protocol. Run the narrowest repository-native test target covering the
   changed behavior, configured syntax/lint/type checks scoped to changed files
   when available, `git diff --check`, and a final review of only this skill's
   touched diff. Prefer no-write and no-cache validation settings in every
   mode; remove only exact task-created validation artifacts and confirm none
   remain before reporting. Do not mark a finding `Fixed` unless its focused
   proof passes. Classify broader failures as caused by the attempted patch,
   independently pre-existing, or unresolved before deciding whether to retain
   the fix. The `code-review` workflow itself must never resolve, load, or
   invoke `align`. Return control after focused validation; the caller or outer
   orchestrator remains responsible for any separate repository policy that
   requires an explicit alignment pass.
10. Preserve every initial finding in the final ledger, update its disposition,
   and state both the initial and final review decisions directly: approve,
   request changes, needs owner review, blocked by missing context, or no
   blocking issue found with residual risks.

## Review Standards

- Be ambitious about simplification. Look for ways to delete complexity, not
  just move it around.
- Be neutral and evidence-based. Critique code behavior and risk, not the
  author or the author's intent.
- Keep priority and auto-fix eligibility independent. A gated P1 remains
  unfixed while a safe P2 may be fixed.
- Treat tests as evidence, not proof. Passing tests reduce uncertainty only
  when they cover the changed behavior and important failure modes.
- Treat ad-hoc conditionals in unrelated flows as design problems unless the
  codebase already uses that pattern intentionally.
- Treat thin wrappers, identity abstractions, pass-through helpers, and generic
  magic as suspicious until they clearly reduce reader burden.
- Push logic toward the canonical layer that owns the concept. Do not normalize
  feature logic leaking through shared APIs or unrelated modules.
- Challenge `any`, `unknown`, casts, nullable modes, optional flags, and silent
  fallbacks when they obscure the invariant.
- Treat a PR or branch pushing a file from below 1000 lines to above 1000 lines
  as a presumptive decomposition concern.
- Flag unnecessary sequential orchestration or partial-update logic when a
  clearer parallel or atomic structure is obvious and behavior can remain the
  same.
- Ask for domain, security, data, or operations owner review when the change
  crosses a boundary that cannot be judged from code evidence alone.

## Guardrails

- Do not invent large rewrites from taste. Findings must point to current code,
  local patterns, or a concrete simpler structure.
- Do not auto-fix gated findings, baseline problems, unknown-attribution
  findings, or out-of-scope code. Report the reason and required owner or user
  decision.
- Do not let direct remediation absorb unrelated dirty files. Preserve and
  report them.
- In report-only modes, do not leave validation caches, reports, generated
  artifacts, dependency changes, or other repository writes behind.
- Do not approve merely because tests pass or behavior appears correct.
- Do not claim code is secure, correct, reliable, production-ready, or
  bug-free beyond the evidence reviewed.
- Do not assume dependency, framework, runtime, or language behavior without
  repository evidence or authoritative documentation when that assumption is
  material to a finding.
- Do not run migrations, contact external services, install packages, rewrite
  history, delete files, or change configuration unless the user explicitly
  asks for that action.
- Do not resolve, load, or invoke `align` from inside `code-review`, claim
  complete project alignment, or use a broad workflow as a fallback for
  missing focused proof. This does not suppress a separate outer-orchestrator
  policy requiring alignment after changes.
- Do not flood the user with low-value naming, formatting, or style nits when
  larger structural issues exist.
- Do not demand abstraction for its own sake. Prefer direct, boring code when
  it removes indirection.
- Do not preserve legacy compatibility layers, aliases, deprecated branches, or
  compatibility wrappers unless the user explicitly requests them.
- Do not expose secrets, private endpoints, customer data, broad raw logs, or
  proprietary internal material in review output or reusable skill sources.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return one final report in chat; do not create a repository report artifact
unless the user separately requests one. Start with scope, initial decision,
final decision, validation results, and finding counts. Then group findings by
P0, P1, P2, P3, and Nit, omitting empty groups. Within a group, list blockers
first and then higher-confidence findings.

For each finding, include:

- stable finding ID and title
- file and line reference when available
- priority and whether it blocks merge or release
- confidence and scope attribution: introduced, baseline, unknown, or out of
  scope
- root cause and evidence
- impact on users, operators, data, security, reliability, or maintainability
- smallest practical remediation or owner decision needed
- verification that should prove the fix
- `Auto-fix: Safe` or `Auto-fix: Gated`, with the reason
- final disposition: `Fixed`, `Needs decision`, `Needs owner review`,
  `Deferred`, or `Not reproduced`

Then include:

- safe changes made and validation performed
- gated or deferred changes and why they were not made
- the exact scope of focused validation and whether broader project alignment
  was intentionally not performed
- residual risks and open questions

## References

- Read `references/quality-rubric.md` for the detailed neutral-review rubric,
  blocker guidance, strict structural standards, and review phrases.
- Use `evals/trigger-prompts.md` when tuning or reviewing implicit invocation
  behavior.
