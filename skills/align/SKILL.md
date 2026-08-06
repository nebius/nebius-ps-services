---
name: align
description: "Use for project-wide alignment or post-change validation: reconcile code, wiring, tests, CI, CLI/help, config, docs, workflows, and project skills; run changed-scope code-review, lint/syntax, security, and regression gates. Integrate current thread, relevant Agent Memory, and task state before low-risk evidence-backed fixes. Do not use for skill-folder-only validation; use align-skill."
---

# Align

## Help

For `$align --help` or `$align -h`, return concise help and stop before
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

Use this skill for safe, end-to-end project alignment and post-change
validation. It coordinates code review as one validation lane; it is not a
replacement for focused `code-review` requests.

The goal is to make the repository consistent, correctly wired, modular where
reasonable, and aligned with the behavior described by its README, design
documents, workflows, tests, CLI/help output, and existing implementation.

This is not a broad rewrite skill or an unconditional full-repository scan.
Treat it as an audit-first, evidence-driven alignment skill that makes minimal,
focused, reviewable changes and finishes with a changed-scope quality gate.

## Use This Skill For

- Scoped repo-wide, service-wide, package-wide, or subsystem-wide consistency
  passes where the affected surface is known or can be inferred
- Requests to reconcile implementation, tests, CI, CLI behavior, config, docs,
  examples, and `--help` output
- Broken wiring, stale imports, duplicate paths, missing registration, flawed
  command flow, or mismatched config/schema behavior
- Cleanup of incorrect, incomplete, stale, misleading, or contradictory
  project surfaces
- Modularity improvements that reduce real inconsistency, duplication,
  coupling, or wiring risk
- "Fix anything inconsistent or incomplete" requests where the agent should
  implement safe fixes, not only report them
- Final post-change quality gates after agent work, including changed-scope
  regression validation, code review, lint or syntax checks, security review,
  focused tests, and residual-risk reporting

## Core Principle

First understand the project contract, then align affected surfaces to that
contract.

The contract must come from repository evidence: README files, design or
architecture documents, source code, tests, fixtures, CI workflows, Makefiles
or task runners, CLI definitions, help output, config schemas, deployment
automation, examples, templates, changelogs, and nearby conventions.

Do not invent requirements. Do not assume docs, tests, or implementation are
automatically correct. Use current, nearby, working code as stronger evidence
than stale prose or isolated examples.

Before proposing fixes, understand the design, data flow, control flow,
dependencies, ownership boundaries, and intended runtime behavior.

## Context Integration

Before deciding what to align, synthesize the active context:

- Read the current user request and current thread for explicit objectives,
  accepted wording, constraints, and newest instructions. Newer user
  instructions override older discussion.
- Use Agent Memory when available and relevant: start with the provided memory
  summary or registry, search only targeted terms, and open pointed rollout or
  skill memory files only when they materially affect the current scope. Treat
  memory as leads, not proof.
- Read injected durable task-state files, workflow run state, or other related
  state files only when active instructions provide a path or the repository
  workflow documents them and they are relevant. Do not invent fallback state
  paths.
- Integrate prior decisions with current repository evidence before editing. If
  the thread, memory, state, docs, tests, and implementation disagree, verify
  with local source or runtime evidence and report unresolved conflicts instead
  of guessing.
- Do not paste or persist raw conversation transcripts, raw memory dumps,
  secrets, customer data, private URLs, or one-off local state in project files
  or task state.

## Changed-Scope Quality Gate

When `align` runs after changes, treat it as the final quality gate for the
affected project surface.

Determine changed scope from the latest user request, touched component roots,
current diff, staged and untracked files, generated artifacts, and any relevant
task state. Separate requested or agent-touched changes from unrelated dirty
worktree files; report unrelated dirty files instead of silently absorbing them
into the alignment scope. Expand the active scope only to direct dependencies,
callers, tests, fixtures, docs, help output, workflows, config, schemas, and
examples that define or consume the changed contract.

Stay incremental and fast. Avoid full-repo scanning by default. Broaden only
when the change touches shared interfaces, central tooling, security-sensitive
surfaces, public contracts, generated artifacts, or an unclear dependency
boundary.

Mandatory lanes for the changed scope:

- Cross-code validation: verify wiring, imports and exports, entry points,
  config flow, CLI/help behavior, docs, tests, and dependent consumers stay
  compatible with the actual contract.
- Code review: load `code-review` in nested report-only mode, read its quality
  rubric for meaningful code changes, and review every changed file or diff
  for correctness, maintainability, modularity, performance, and structural
  regressions. `align` owns any safe remediation. The child review must not
  edit files, invoke `align`, or interpret its nested use as a direct
  `$code-review` invocation.
- Lint and syntax: load `linter` for Shell, Markdown, and Python surfaces.
  Prefer check-only scoped validation first. When using the bundled linter
  script as a validation gate, start with `--no-fix --no-config-fallback`; use
  fix mode only when the current `align` task is already allowed to patch safe
  issues. Prefer repository-native no-cache settings and remove only exact
  task-created validation artifacts before completion.
- Security: load `apply-security` and run a changed-scope security review for
  infrastructure, deployment, workflow, shell, and application surfaces. Apply
  only the low-risk fixes allowed by `apply-security`'s safe auto-fix policy.
  Plan first, report, or block on high-risk or externally visible public
  exposure, IAM/RBAC, auth, crypto, serialization, database, availability,
  credential, or external-route changes that require explicit approval.
- Tests and builds: run the narrowest repository-native tests, type checks,
  build checks, smoke commands, help renders, or dry runs needed to validate
  affected behavior. Broaden only when shared contracts or high-risk surfaces
  changed.

A request to use `align` authorizes it to coordinate `code-review`, `linter`,
and `apply-security` as validation lanes inside `align`'s current changed
scope. Keep each child skill's stricter safety and no-auto-fix rules. Use the
nested report-only `code-review` lane defined above; only `align` may apply its
resulting safe findings.

`apply-security` may be selected implicitly outside `align`; inside `align`, it
is mandatory. If it is not visible in the initial skills list because of
skill-list budget, installation, or discovery limits, resolve and read its
`SKILL.md` directly:

1. Use the current session's skill path when available.
2. Otherwise try the sibling path next to this skill, such as
   `../apply-security/SKILL.md`.
3. If the sibling path is unavailable, search readable standard Codex skill
   locations, including repo-local skill roots and
   `$HOME/.agents/skills/apply-security`.

After loading `apply-security`, follow its required-reference rules for the
security lane. If it cannot be found or read, report the security lane as
blocked or skipped with the paths checked; do not report that alignment is
complete.

Default remediation policy is safe-only: fix clear, low-risk issues inside the
current changed scope; report blockers and request explicit approval for risky,
ambiguous, public-contract, architecture, or security-sensitive changes.

When a parent workflow supplies a finding ledger, preserve its IDs,
classifications, and dispositions as handoff constraints. Do not remediate or
reclassify parent-gated, owner-review, decision-required, or deferred findings
unless the user separately authorizes them. `align` may repair only other
clear low-risk findings inside the changed scope; its own safe-only policy must
not weaken the parent's stricter exclusion.

For the detailed changed-surface checklist, review focus, wiring checks, and
modularity guidance, read `references/quality-gate.md` while mapping or
validating the affected scope.

## Safety Rules

- Inspect before changing.
- Prefer minimal, targeted changes.
- Preserve intended behavior unless there is clear evidence of a bug,
  inconsistency, stale contract, or broken wiring.
- Preserve unrelated user changes. Treat unrelated dirty files as context to
  report, not as permission to edit them.
- Only change code when the issue is clear and the fix is low risk.
- Do not perform broad rewrites for style alone.
- Do not rename public APIs, commands, flags, environment variables, files,
  modules, workflows, or documented behavior unless all affected references are
  updated.
- Do not change public APIs, data contracts, database schemas, authentication,
  authorization, external integrations, business rules, security, secrets
  handling, persistence, migrations, deployment, or production workflows unless
  there is a clear reason, the impact is documented, and focused validation is
  run.
- Do not delete code, files, configs, tests, or docs unless they are proven
  stale, unreachable, duplicate, or harmful.
- Prefer one canonical path over compatibility shims unless the user
  explicitly requests legacy support.
- Do not hide uncertainty with speculative docs, tests, comments, fallbacks, or
  silent defaults.
- Do not let Agent Memory, prior task state, stale docs, or prior discussion
  override current codebase evidence without verification.
- Do not introduce new dependencies unless they are necessary and consistent
  with the project.
- If behavior changes, explain the before-and-after behavior.
- If business logic is unclear, leave it unchanged and document the
  uncertainty, risk, and recommended follow-up.
- Keep changes easy to review.

When uncertain, report the uncertainty instead of changing behavior.

## Workflow

1. Consolidate current and durable context.
   Review the current thread for the user's latest objective and constraints,
   then gather relevant Agent Memory and related task or workflow state when
   available. Keep only a concise decision summary in the parent thread or task
   state; do not copy raw discussions, broad memory dumps, logs, secrets, or
   environment-specific values.
   Identify unrelated dirty files early and keep them out of scope unless the
   user explicitly broadens the task to include them.
2. Inspect before editing.
   Map the project layout, runtime entry points, main libraries or services,
   data flow, control flow, dependencies, test strategy, lint or format
   tooling, and CI workflows. Read enough code and docs to understand intended
   behavior and how the pieces connect.
3. Check applicable skills.
   For post-change validation, always apply the `code-review`, `linter`, and
   `apply-security` lanes described above. For other subtasks, use additional
   skills only when their metadata clearly matches a concrete part of the task,
   such as workflow, Helm, shell, Terraform, Python, documentation,
   spreadsheet, slide, PDF, or repo-local skill work. Read the relevant
   `SKILL.md`; use the direct `apply-security` resolution path above when the
   security lane is not visible in the initial skill list. Keep scope narrow,
   and do not let another skill override these safety rules unless the user
   explicitly asked for that behavior. Invoke `code-review` through the nested
   report-only lane defined above.
4. Establish the actual contract.
   Compare implementation against tests, CLI help, examples, workflows, and
   documentation. Treat mismatches as evidence to resolve, not as proof that
   any one surface is automatically correct.
5. Identify and prioritize alignment gaps.
   Prioritize:
   - bugs and incorrect behavior
   - broken runtime or workflow paths
   - missing or inconsistent configuration flow
   - flawed wiring or duplicate implementations
   - stale tests that hide real behavior
   - missing tests for repaired behavior
   - stale docs, examples, help output, or changelog entries
   - modularity issues that directly cause maintenance risk
6. Make focused changes.
   Make the smallest effective change only when the issue is clear and the
   fix is low risk. When behavior changes or a bug is fixed, update the
   relevant tests, docs, examples, help text, and workflow assumptions so the
   project has one canonical story.
7. Verify.
   Run the mandatory changed-scope quality gate: cross-code validation,
   code-review findings, lint or syntax checks, security review, focused tests,
   type checks, build checks, and relevant `--help` or smoke commands for the
   touched surfaces. Broaden only when shared contracts, high-risk surfaces,
   generated artifacts, or unclear dependency boundaries require it. If the
   repo has a `CHANGELOG.md`, update the active unreleased section when
   behavior, commands, workflows, or user-facing docs changed. Check the final
   diff for unrelated files, secrets, credentials, private endpoints, and
   environment-specific values. Run the nested report-only `code-review` lane
   against the final diff after any align-owned safe repairs. Remove only exact
   task-created validation caches, reports, or generated artifacts, then
   confirm repository status contains no new validation residue.
8. Report remaining uncertainty clearly.
   If something cannot be verified from the codebase or local tooling, say
   exactly what could not be confirmed and why. Do not guess.

## Required Behaviors

- Review and fix issues. Do not stop at a discrepancy list unless the user
  explicitly asks for audit-only output.
- Update tests to match intended behavior.
- Update documentation and help text to match the implementation.
- Keep commands, flags, examples, and docs aligned.
- Remove dead ends, broken flows, contradictions, and obvious bugs.
- Preserve intended logic unless the current logic is clearly incorrect or the
  user explicitly asked to change it.
- Verify wiring from entry point to implementation where relevant.
- Apply relevant available skills for concrete subtasks, and always apply
  `code-review`, `linter`, and `apply-security` as changed-scope validation
  lanes before considering post-change alignment complete.
- Keep remediation ownership in `align`; the nested `code-review` lane returns
  findings only.
- Prefer no-write and no-cache validation settings; do not leave task-created
  validation artifacts in the repository.
- Base alignment decisions on the current thread, relevant Agent Memory, and
  related state files only after checking them against current repository or
  runtime evidence.
- Do not invent requirements.
- Do not assume missing behavior is correct.
- State clearly when something cannot be verified from the codebase.

## Prohibited Behaviors

Do not:

- perform a large rewrite without evidence
- change public contracts casually
- change business logic silently
- delete files because they look unused without checking references
- add fallback behavior that hides broken wiring
- update docs to match broken code when the code is clearly wrong
- update tests to match a bug
- treat Agent Memory, old task state, or previous discussion as unquestioned
  proof of current behavior
- persist raw discussion, memory, or state-file contents into project files,
  generated docs, task state, or final reports
- optimize speculative paths at the cost of clarity
- ignore README, design documents, CI, workflow references, or help output
- assume generated code should be edited manually
- claim validation passed when it was not run
- claim complete alignment if important areas were not inspected
- claim the post-change quality gate is complete if code-review, lint/syntax,
  security, cross-code validation, or focused tests were skipped without an
  explicit limitation

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

When using this skill:

1. Make safe fixes first when the issue is clear.
2. Use this report shape for broad alignment/code-review requests:
   - Summary of findings
   - Critical issues that must be fixed
   - Bugs or dead-end flows found
   - Performance and optimization opportunities
   - Consistency and alignment issues
   - Tests that are missing or should be improved
   - Code-review, lint/syntax, security, cross-code, and test/build gate results
   - Safe code changes made, including why each change is safe
   - Changes intentionally not made because the logic was unclear
   - Context used from the current thread, relevant Agent Memory, or task state,
     including any conflicts or stale information that affected decisions
   - Risks, assumptions, and open questions
   - Final recommendation
3. Include applicable skills used and validation actually run.
4. Call out anything still unverified or intentionally deferred.
