# Skills

This folder contains public, reusable Codex skills for common engineering
workflows. Each skill lives in its own folder and is discovered by the presence
of `SKILL.md`.

This root README is the concise catalog and install guide. Most skill folders
also have a local `README.md` that explains architecture, core concepts,
workflow, and important files. `SKILL.md` remains the runtime instruction file
Codex loads when the skill is used.

Every reusable skill includes a `## Learning Loop` section in `SKILL.md`. When
durable, public-safe, evidence-backed knowledge is discovered while using a
skill, the agent should capture it in the narrowest appropriate source material
for that skill when the task contract allows source edits. For read-only or
report-only work, the agent should report why source capture was skipped.
`align-skill` can add or repair this rule during skill alignment.

For skill-specific release notes, see [CHANGELOG.md](CHANGELOG.md).

## Table of Contents

- [Skill Catalog](#skill-catalog)
- [Using Skills in Codex Chat](#using-skills-in-codex-chat)
- [Skill Details](#skill-details)
- [Skills Installer](#skills-installer)

## Skill Catalog

The catalog below mirrors the live skill folders in this source tree. The
`Invocation` column reflects `agents/openai.yaml`:

- `Implicit allowed`: Codex may select the skill when the prompt matches.
- `Explicit only`: invoke the skill directly with `$skill-name`.

### Alignment and Authoring

| Skill | Invocation | Description |
| --- | --- | --- |
| `align` | Implicit allowed | Project-wide alignment and changed-scope quality gates across code, wiring, tests, CI, CLI/help, config, documentation, workflows, project skills, code review, lint/syntax, and security. |
| `align-skill` | Implicit allowed | Review, harden, validate, and improve existing or newly scaffolded Codex or Agent Skill folders after an initial scaffold or draft exists. |
| `brainstorm` | Implicit allowed | Explore ideas in chat with source-ranked project, repo, skill, internal, vendor, and advisory design-skill context before implementation. |
| `code-review` | Implicit allowed | Neutral findings-first review of local diffs, local branches, changed files, modules, repository areas, or patches for bugs, tests, reliability, maintainability, and structural simplification. |
| `create-learning-course` | Explicit only | Create public-safe learning courses, course workspaces, syllabi, lessons, exercises, glossaries, and publication review checkpoints. |
| `global-context-management` | Implicit allowed | Keep complex Codex tasks focused with durable task state, concise parent-thread context, targeted read-only subagents when authorized, focused validation, and final risk review. |
| `research` | Implicit allowed | Senior-engineer technical due diligence on technologies, APIs, RFCs, protocols, architecture patterns, products, and feature requirements. |

### Local Setup and Information

| Skill | Invocation | Description |
| --- | --- | --- |
| `agent-nebius-auth` | Explicit only | Bootstrap, repair, verify, or install Codex Agent Nebius service-account authentication and its token-injection hook. |
| `agentic-sdlc-test` | Explicit only | Verify the Agentic SDLC system from outside the workflow with disposable hook fixture tests, golden-path guidance, and a safe local report. |
| `attach-ubuntu` | Explicit only | Launch or reuse a disposable Ubuntu Docker container for the current project and best-effort open it through VS Code Dev Containers. |
| `code-info` | Explicit only | Produce read-only, copy/paste-friendly code metrics for local folders or GitHub repositories without changing files. |
| `config-codex` | Explicit only | Configure a public-safe local Codex home setup, including global policy, MCP config, hooks, task-state layout, custom read-only agents, and validation. |
| `install-grafana-mcp-for-nebius` | Explicit only | Install and configure the official Grafana MCP server for Codex against Nebius-managed Grafana observability data. |

### Git, Pull Requests, and Publishing

| Skill | Invocation | Description |
| --- | --- | --- |
| `commit` | Explicit only | Create one fast local Git commit on the current branch after repo-root `git add -A` and lightweight staged validation; never pushes. |
| `commit-push` | Explicit only | Commit all current feature-branch changes from the repo root and push the branch to `origin` without opening a pull request. |
| `create-pr` | Explicit only | Create or reuse GitHub pull requests from local work or named branches, with branch-safe preparation, validation, and readiness reporting. |
| `merge-pr` | Explicit only | Verify and merge a ready GitHub pull request without admin bypass after checking reviews, checks, mergeability, branch state, and head SHA. |
| `publish-helm` | Explicit only | Publish an OCI Helm chart end to end: prepare release changes, PR/merge, tag, wait for workflow, verify the chart, and report the result. |
| `publish-image` | Explicit only | Publish a container image end to end: prepare release changes, PR/merge, tag, wait for workflow, verify image tags/digest, and report the result. |
| `publish-release` | Explicit only | Publish a GitHub Release end to end: prepare release changes, PR/merge, tag, wait for workflow, verify assets, and report the result. |
| `review-pr` | Explicit only | Review a GitHub pull request by number, URL, or current branch, fix safe issues when possible, and report merge readiness or blockers. |

### Project Engineering

| Skill | Invocation | Description |
| --- | --- | --- |
| `apply-security` | Implicit allowed | Advise on, review, and safely remediate security issues across design, implementation, infrastructure, deployment, Helm, Kubernetes, Terraform, CI/CD, shell, and application code. |
| `design` | Implicit allowed | Design software features, applications, components, APIs, data flows, and technology choices before implementation, ending with a `/plan` handoff. |
| `github-workflows` | Implicit allowed | Create, review, or standardize GitHub Actions for PR/merge CI, merge automation, reusable workflows, permissions, and release/image YAML. |
| `gitignore` | Implicit allowed | Create or update stack-aware `.gitignore` files with sensible macOS, VS Code, and detected language/tool defaults. |
| `helmchart` | Implicit allowed | Create, review, harden, refactor, lint, template, or standardize Helm charts and chart CI. |
| `linter` | Implicit allowed | Lint and conservatively auto-fix shell, Markdown, and Python files with tools such as `shellcheck`, `markdownlint`, and Ruff. |
| `nebius` | Implicit allowed | Automate Nebius SDK/cloud workflows for IAM, object storage, VPC, quota, MK8s readiness, GPU/operator decisions, and observability wiring. |
| `nebius-audit-log` | Explicit only | Query Nebius Control Plane Audit Logs by resource or current subject with bounded, sanitized read-only CLI output. |
| `python-project` | Implicit allowed | Scaffold or harden Python projects with modern packaging, `src/` layout, Ruff, pytest, Typer, Pydantic, services, APIs, and CI. |
| `shell-scripting` | Implicit allowed | Create, refactor, or review Bash automation with strict mode, safe argument parsing, idempotency, and readable CLI output. |
| `system-design-rules` | Implicit allowed | Evaluate system designs, ADRs, architecture options, APIs, data ownership, reliability, security, observability, scale, cost, and team boundaries with a practical design checklist. |
| `task-implementer` | Implicit allowed | Break brownfield implementation-loop requests into ordered `task-1`..`task-n` work items and run them sequentially with markdown handoff checkpoints between fresh Codex sessions. |
| `terraform` | Implicit allowed | Scaffold, standardize, or improve Terraform repositories and modules with state guidance, validation, security controls, examples, and CI. |

### Agentic SDLC Workflow

All `sdlc-*` skills are explicit-only and should run through the Agentic SDLC
workflow, normally starting with `$sdlc-start`.

| Skill | Invocation | Description |
| --- | --- | --- |
| `sdlc-align-specs` | Explicit only | Check SDLC requirements, design, plans, tests, implementation, and evidence for consistency. |
| `sdlc-classify-failure` | Explicit only | Classify failed SDLC phases and route the loop to the earliest responsible phase. |
| `sdlc-commit` | Explicit only | Create a local feature-scoped SDLC commit after validation, tests, and evaluation pass; never pushes. |
| `sdlc-create-design` | Explicit only | Create or update `docs/design.md` from requirements and gathered context, preserving stable feature IDs. |
| `sdlc-create-plan` | Explicit only | Create a locked private local execution plan for one ready feature before tests or implementation. |
| `sdlc-create-requirements` | Explicit only | Create or update `docs/requirements.md` from user prompts, tickets, stories, or change requests while preserving stable requirement IDs. |
| `sdlc-evaluate` | Explicit only | Evaluate whether the current feature solves the real-world requirement using acceptance criteria and the right harness. |
| `sdlc-gather-context` | Explicit only | Build compact feature context packs from product, vendor, internal, codebase, and test sources. |
| `sdlc-gui-test` | Explicit only | Control and evaluate browser UI behavior against SDLC acceptance criteria with screenshots or accessibility snapshots when available. |
| `sdlc-implement-plan` | Explicit only | Implement production code for the current locked feature plan after `sdlc-tdd`, staying inside plan boundaries. |
| `sdlc-merge-pr` | Explicit only | Merge a specific Agentic SDLC pull request only after explicit user request and final readiness checks. |
| `sdlc-start` | Explicit only | Start, resume, or continue the Agentic SDLC workflow and choose the next phase from local run state. |
| `sdlc-tdd` | Explicit only | Convert acceptance criteria and design success criteria into failing or already-green tests before implementation. |
| `sdlc-tui-test` | Explicit only | Control and evaluate terminal, CLI wizard, or TUI flows with transcript and exit-code evidence. |
| `sdlc-uat-tests` | Explicit only | Run product-level user acceptance testing across the whole system before PR creation. |
| `sdlc-unit-tests` | Explicit only | Run behavior, regression, integration, component, contract, or mock-based tests for the current feature. |
| `sdlc-validate-codes` | Explicit only | Run build, parse, lint, type, import, dependency, and configuration validation for the current feature. |

## Using Skills in Codex Chat

For deterministic explicit invocation, use the exact skill name with a leading
`$` in the Codex chat box, then add the task you want. For example, use
`$align` for this project, `$shell-scripting` to harden a Bash script, or
`$terraform` to scaffold or review Terraform code. Official Codex docs also
describe explicit invocation as including the skill directly in the prompt;
using `$skill-name` remains the clearest repo convention.

OpenAI Codex treats `agents/openai.yaml` as optional skill metadata for UI,
invocation policy, and dependencies. In this repository, every source skill
must keep that file so invocation policy and useful interface metadata can be
reviewed and validated. Skills marked `Explicit only` in the catalog use
`allow_implicit_invocation: false` and should be started explicitly with
`$skill-name`. Skills marked `Implicit allowed` use
`allow_implicit_invocation: true` so Codex may select them when the task
matches their metadata.

For structure, the OpenAI portable minimum is a skill folder with `SKILL.md`
containing front matter `name` and `description`. This repository uses a
stricter source-owned standard: `agents/openai.yaml` for metadata and
invocation policy, a standard `## Learning Loop`, and optional `references/`,
`scripts/`, `assets/`, and `evals/` only when they serve the skill.

### Prompt Examples

```text
$commit Quickly commit all current local changes on this branch without pushing.

$commit-push Commit all current changes on this feature branch, generate a commit message, push it to origin, and tell me whether the worktree is clean.

$create-pr Create a PR for the current local work, using a new prep branch if I am still on the default branch.

$create-pr Resolve conflicts for the current branch against main, open or reuse its PR, and return the PR URL.

$review-pr Review PR #110 against the base branch, fix safe issues on the branch, and tell me whether it is ready to merge.

$review-pr Review https://github.com/example-org/example-repo/pull/42, resolve straightforward conflicts against main if the branch is writable, and report remaining blockers.

$merge-pr Merge PR #110 with squash after verifying checks, reviews, mergeability, and the head SHA without using admin bypass.

$publish-image --mode complete --tag 1.2.3 --image-name ghcr.io/example-org/example-app prep, PR, merge, tag, wait for CI, verify the image digest, and report the published artifact.

$agentic-sdlc-test Verify the Agentic SDLC workflow against docs/agentic-sdlc-design.md and write a safe report.

$align-skill Review and standardize skills/foo against the canonical skill structure and official vendor docs.

$align-skill Harden this scaffolded skill folder into a safe, secure, fast Codex skill, then validate it.

$brainstorm Explore this architecture idea, gather the relevant project docs, related skills, internal context, and official vendor docs, and challenge weak assumptions before we implement anything.

$create-learning-course Create a public-safe course workspace for engineers learning Kubernetes networking, with mission, syllabus, sources, HTML lessons, exercises, glossary, and publication review.

$research Research Kubernetes Gateway API, explain how it works internally, identify limitations and alternatives, and recommend when we should or should not use it.

$design Design this new feature before implementation: read the requirements, inspect the existing code and docs, route unfamiliar topic and technology research through $research, compare options, and create a /plan handoff.

$code-review Review the current local branch for bugs, regressions, test gaps, reliability risks, maintainability blockers, and missed structural simplifications.

$system-design-rules Review this ADR against the system design checklist, compare the trade-offs, and identify missing reliability, data, security, observability, cost, and ownership decisions.

$task-implementer Break this brownfield change into ordered task-1..task-n work items, implement them sequentially with a markdown handoff, and use a fresh Codex session for each task.

$apply-security Scan this repository for infrastructure, CI/CD, shell, and application security issues, then produce a prioritized remediation plan with safe patch candidates.

$code-info Gather read-only project info from this folder or a GitHub repo with LOC by language and component, repo size, test files, CLI commands, modules, artifacts, and coverage.
```

You can also be more specific when needed:

```text
$create-pr Open or reuse the PR for this branch with title "Expose nccl-test 0.2.7 in the bundled catalog" and return the PR number and URL.

$create-pr Create conflict-free PRs for branches feature-a and feature-b against main, and tell me the merge order.

$review-pr Review this Helm chart PR, apply the relevant sibling skills, resolve straightforward conflicts if they exist, and rerun the focused validation.
```

These prompts should work when the skill is installed and the local environment
matches the task. For Git-backed flows such as `commit`, `commit-push`,
`create-pr`, `merge-pr`, and `review-pr`, that means the current directory is
inside a Git repository. For remote-backed flows such as `commit-push`,
`create-pr`, `merge-pr`, `review-pr`, and the `publish-*` complete flows, that
also means:

- the repository has an `origin` remote
- the branch state allows the requested operation

For GitHub CLI backed flows such as `create-pr`, `merge-pr`, `review-pr`, and
the `publish-*` complete flows, that also means:

- `gh` is authenticated for the target repository

If those prerequisites are missing, the skill should stop and explain the
blocker instead of guessing.

Skill-bundled scripts may be executed for read-only inspection or validation
when they do not modify files, services, external systems, credentials, or
persistent state. Execute scripts that make changes only when the user
explicitly starts the request with `Run` or `Execute` (or equivalent);
otherwise read the script or report the command that would be used.

## Skill Details

The catalog above is the complete live index. This section gives extra context
for the broader, commonly reused skills and groups the Agentic SDLC phases
together so the workflow reads as one lifecycle.

### `align`

`align` is the end-to-end repair, consistency, and post-change quality-gate
skill. Use it when a project needs code, module wiring, tests, CI, CLI
behavior, config, examples, help output, README/design docs, workflows, and
applicable project skills reviewed together as a cautious senior alignment
pass. It synthesizes the current thread, relevant Agent Memory, and durable
task-state context, separates active scope from unrelated dirty files, and
verifies that context against current repository or runtime evidence before
making safe fixes. Before completion, it runs mandatory changed-scope lanes for
cross-code validation, `code-review`, `linter`, `apply-security`, and focused
repository-native tests or builds. It uses safe-only remediation, reports risky
blockers for explicit approval, and resolves `apply-security/SKILL.md` directly
when the mandatory security lane is not visible in the initial skills list.

### `align-skill`

`align-skill` reviews and hardens one or more existing or newly scaffolded Codex
or Agent Skill folders. Use it for named skills, local skill folders,
multi-skill parent folders, GitHub skill repositories, or GitHub tree URLs when
`SKILL.md`, trigger metadata, references, assets, scripts, safety guardrails,
official vendor-doc verification, canonical structure, validation evidence, fast
authoring practices, optional stateful-workflow section profiles, and reusable
learning capture in local skill source materials need to be aligned.

### `brainstorm`

`brainstorm` supports evidence-first, chat-only ideation before implementation.
It restates the topic, builds a compact source plan, gathers context from the
current project folder first, then sibling repo folders, related skills,
internal Confluence/Slack/Jira sources when available, and official vendor
docs. It separates facts from hypotheses, challenges weak assumptions, compares
options, consults `design` and `system-design-rules` for major decisions when
those skills are installed and accessible, and stops short of editing files,
creating tickets, sending messages, or mutating external systems. If the user
pivots to execution, it should
summarize the brainstorm and hand off to the appropriate implementation,
alignment, SDLC, or communication skill.

### `create-learning-course`

`create-learning-course` turns a learner mission, trusted source set, target
audience, and desired outcomes into a public-safe course workspace. It creates
or revises artifacts such as `MISSION.md`, `COURSE.md`, `SYLLABUS.md`,
`RESOURCES.md`, HTML or Markdown lessons, exercises, glossaries, reference
sheets, learning records, reusable assets, and `PUBLICATION-REVIEW.md`.
Invoke it explicitly with `$create-learning-course`; implicit invocation is
disabled because the workflow can create or revise many local files.
It is our course-authoring workflow: mission-led, source-grounded,
practice-heavy, and backed by explicit redaction, source-citation,
high-stakes-topic, and publication-safety checks so generated courses avoid
secrets, private endpoints, customer data, raw logs, and proprietary internal
material. Its detailed workflow reference keeps attribution for the public
teaching-skill pattern that inspired the learning model.

### `research`

`research` performs senior-engineer technical due diligence on technologies,
frameworks, APIs, protocols, RFCs, products, architecture patterns, and feature
requirements. It ranks sources by authority, starts from official docs,
specifications, papers, and official source code, then uses implementation and
community sources for maintainer intent and operational pain points. The output
explains core concepts, architecture, internals, operations, limitations,
alternatives, and recommendations for design decisions. Use `brainstorm` for
open-ended ideation, `design` for solution design and `/plan` handoff, and
`system-design-rules` for checklist review of an existing proposal.

### `design`

`design` turns requirements, existing-system evidence, and `research`-backed
topic, requirement, and technology due diligence into a concrete software
design before implementation. It follows six phases: understand requirements,
understand the existing system or greenfield context, route missing knowledge
through `research` when available, design the solution, evaluate alternatives,
and create a Codex `/plan` handoff. Use it for new features,
major changes, APIs, data flows, integrations, and new applications when the
user wants a practical design and implementation-ready plan, not immediate
coding. Use `brainstorm` for open-ended ideation, `system-design-rules` for
checklist review of an existing proposal, and `sdlc-create-design` for
Agentic SDLC-owned `docs/design.md`.

### `code-review`

`code-review` performs a neutral, evidence-based review of the current local
branch, local diff, changed files, module, repository area, or provided patch.
Use it when the user wants findings-first feedback on bugs, regressions, test
gaps, reliability risks, security-adjacent issues, maintainability,
abstraction quality, modularity, type boundaries, file-size growth, spaghetti
branches, and missed structural simplifications. It is review-first and should
not edit code unless the user explicitly asks for fixes. `review-pr` remains
the GitHub PR review, readiness, and branch-update workflow, while `align`
remains the broader project consistency and repair workflow. Use
`apply-security` for security-specific scans, threat modeling, and remediation,
and `system-design-rules` for design-phase architecture decisions.

### `system-design-rules`

`system-design-rules` applies a refined 100-rule software system design
checklist to architecture proposals, ADRs, design docs, API and data model
choices, reliability plans, security posture, observability, performance,
cost, migration, and team-ownership decisions before implementation. It scales
review depth to risk, separates facts from assumptions, compares options by
trade-offs and reversibility, and returns concrete design guidance, validation
needs, and open questions instead of treating principles as universal laws.

### `task-implementer`

`task-implementer` coordinates small and medium brownfield implementation
requests that need ordered execution across fresh Codex contexts. It inspects
the target code before ordering work, records a `task-1` through `task-n` queue
in a private markdown handoff under `$CODEX_HOME/task-implementer/`, implements
one task at a time, and updates the handoff after every task with changed files,
validation, blockers, and the next-session prompt. Because it mutates code and
may launch follow-on implementation sessions, implicit selection is limited to
prompts that ask for sequential task-loop execution. Use `$sdlc-start` for
Agentic SDLC and `align` for final changed-surface alignment.

### `agentic-sdlc-test`

`agentic-sdlc-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks `docs/agentic-sdlc-design.md`, global `sdlc-*` skill
discovery, explicit-only SDLC invocation policy, hook configuration,
disposable PreToolUse and Stop hook fixture behavior, idempotency, failure
routing, steering, and disposable golden-path execution. It writes the report
under `~/.codex/sdlc-verification/` and must not change real projects,
installed skills, hooks, hook trust, or agent configuration.

### `agent-nebius-auth`

`agent-nebius-auth` is a setup-only skill for bootstrapping or repairing local
Codex Agent Nebius authentication. It uses a service account, tenant group,
project-level access permit, authorized-key credential file, CLI profile, and a
Codex `PreToolUse` hook that injects short-lived Nebius token environment
variables into matching Bash commands without returning token material as model
context. The hook also exports the agent credential file path and wires a Bash
`nebius_refresh_token` helper through a restricted temporary `BASH_ENV` file
for long-running raw API scripts. Install or refresh the hook through the root
installer, for example
`./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks`;
the setup script does not patch `$CODEX_HOME/config.toml` and instead records
the selected project under `~/.nebius` for the hook to read locally.

### Agentic SDLC Skills

The Agentic SDLC skills implement a skill-driven state machine for turning user
ideas into requirements, design, locked local plans, test-first implementation,
validation, evaluation, local commits, UAT, PR creation or reuse, PR review,
and explicit final merge.
Strictly SDLC-only skills use the `sdlc-` prefix, with the coordinator named
`sdlc-start`, so tool discovery does not confuse workflow phases such as
`sdlc-commit` with ordinary Git commands or general-purpose engineering skills.
All `sdlc-*` skills set `allow_implicit_invocation: false`; start or resume the
workflow explicitly with `$sdlc-start`, then let the coordinator record the next
recommended phase in local run state.
The committed product truth is `docs/requirements.md` and `docs/design.md`;
private run state, plans, evidence, screenshots, transcripts, and steering live
under `~/.codex/sdlc-runs/<project-id>/<run-id>/` and must not be committed.
Optional global PreToolUse and Stop hooks can enforce SDLC invariants from that
local state. The Stop hook routes continuation through explicit `$sdlc-start`
invocation rather than directly into phase skills. Sensitive Git actions use
short-lived local authorization files under the active run's `permissions/`
directory; the skills create those files only immediately before the guarded
action.
The canonical source for those optional SDLC hooks is
`sdlc-start/assets/hooks/`. Patch that source first, validate it with
`sdlc-start/assets/hooks/tests/test_sdlc_hooks.py`, and sync reviewed hook
bundles deliberately with `./install-skills.sh --install-all-hooks`; installed
copies under `$CODEX_HOME/hooks` are runtime artifacts.
Keep these SDLC hooks separate from the non-SDLC global-context hooks:
`SessionStart` is for stable global context and task-state location, and
`UserPromptSubmit` is only for lightweight prompt-time context, safety, or
opt-in delegation requests.
See `docs/agentic-sdlc-design.md` for the architecture, template ownership,
local state layout, hook boundaries, and full skill-by-skill lifecycle.

- `sdlc-create-requirements`: creates or updates `docs/requirements.md` from user
  prompts, tickets, or approved change requests while preserving stable
  `REQ-*` IDs.
- `sdlc-start`: coordinates the active SDLC run, reads steering and local
  checkpoints, selects the highest-priority incomplete feature, and chooses one
  next skill without duplicating history on unchanged resumes.
- `sdlc-gather-context`: builds compact feature context packs from official docs,
  internal sources, code, and tests.
- `sdlc-create-design`: creates or updates `docs/design.md`, maps requirements to
  stable `FEAT-*` blocks, and defines validation, test, and evaluation plans.
- `sdlc-create-plan`: creates locked private local execution plans for one feature.
- `sdlc-tdd`: writes or maps tests before implementation.
- `sdlc-implement-plan`: implements production code for the current locked feature
  plan only.
- `sdlc-validate-codes`: runs syntax, lint, type, import, config, dependency, and
  build checks where configured.
- `sdlc-unit-tests`: runs feature behavior, regression, integration, component,
  contract, or mock-based tests.
- `sdlc-evaluate`: observes feature behavior against acceptance criteria and routes
  to GUI, TUI, API, service, or manual evaluation.
- `sdlc-align-specs`: checks SDLC requirements, design, plans, tests,
  implementation, and evidence for consistency before commit or PR readiness.
- `sdlc-classify-failure`: classifies failed phases before retrying and routes to
  the earliest responsible SDLC phase.
- `sdlc-gui-test`: controls and evaluates browser UI flows with screenshots or
  accessibility snapshots when available.
- `sdlc-tui-test`: controls and evaluates terminal, CLI wizard, or TUI flows with
  transcripts and exit-code evidence.
- `sdlc-commit`: creates local feature-scoped Git commits after validation, tests,
  and evaluation pass; it never pushes and does not replace the general
  `commit` or `commit-push` skills.
- `sdlc-uat-tests`: runs product-level user acceptance testing before PR creation.
- `create-pr`: existing PR skill reused as the SDLC handoff after UAT passes;
  it opens or reuses the PR and summarizes SDLC evidence.
- `review-pr`: existing PR review skill reused for SDLC merge-readiness review
  against specs, checks, reviews, and local evidence.
- `sdlc-merge-pr`: merges a specific PR only after an explicit user request and
  final readiness checks.

### `apply-security`

`apply-security` reviews infrastructure, deployment, Helm, Kubernetes,
Terraform, CI/CD, Bash, Python, Java, JavaScript, TypeScript, and Rust code for
security issues. It can be selected implicitly during design, implementation,
review, and validation sessions as a security adviser. It ranks findings by
severity, confidence, exploitability, and blast radius, plans safe
remediations, and applies minimal patches only when the current task allows
edits and the change preserves intended behavior or has explicit approval.

### `attach-ubuntu`

`attach-ubuntu` launches or reuses a per-project `ubuntu:24.04` Docker
container, mounts the project at `/workdir`, prepares attached-container VS
Code defaults, and helps create a disposable Ubuntu environment for local
testing on macOS with Docker Desktop and the Dev Containers extension.

### `commit`

`commit` creates a fast local Git commit on the current branch without pushing.
It stages the complete monorepo diff with repo-root `git add -A`, runs
lightweight staged validation, uses a provided or generated commit message,
preserves normal hooks, and stops instead of pushing, creating PRs, repairing
branches, or writing Agentic SDLC evidence.

### `commit-push`

`commit-push` commits all current local changes on the active non-default
feature branch and pushes that branch to `origin`. It stages the complete
monorepo diff with `git add -A`, generates a commit message when needed, runs
lightweight Git validation, preserves normal hooks, and stops instead of
pulling, rebasing, merging, force-pushing, or opening a PR.

### `create-pr`

`create-pr` turns local work or named branches into GitHub pull requests
without leaving new work on the default branch. It can prepare conflict-free
PRs, repair safe branch-owned validation or GitHub check failures before
presenting the PR as handled, avoid duplicate PRs for the same head branch,
preserve one PR per branch, stage complete monorepo local work with
`git add -A` only after safe formatting, whitespace, lint, build, and focused
test checks finish, validate the staged diff, merge `origin/<base>` into the PR
branch before PR creation without rewriting history, reuse the current
non-default branch without creating another branch, push with explicit
refspecs, wait for available GitHub checks before calling the PR ready, and
report readiness plus manual merge order.

### `merge-pr`

`merge-pr` verifies and merges a GitHub pull request outside the Agentic SDLC
workflow. It checks PR metadata, checks, review state, mergeability, base
branch, and the exact head SHA, waits for pending checks when useful, then
merges with `gh pr merge --match-head-commit` using `squash` by default, or the
no-strategy merge-queue path when the base branch requires one. It does not use
admin bypass, force-push, delete branches by default, or merge when branch
protection, required reviews, environment approvals, conflicts, or failing
checks still block the PR.

### `code-info`

`code-info` summarizes a local project folder or a GitHub repository with
read-only, copy/paste-friendly Markdown metrics, including LOC per language,
LOC per top-level component, tracked repo size, repo link, test file counts,
CLI command definitions, package/module counts, build artifact sizes, and
already-available coverage artifacts. For not-yet-cloned GitHub repositories,
it reads a temporary archive using `GH_TOKEN`, `GITHUB_TOKEN`, or
`gh auth token` when needed. It does not edit, format, build, test, install,
generate coverage, or stage files.

### `config-codex`

`config-codex` bootstraps or aligns a user's local Codex runtime setup from
public-safe templates. Use it for `$CODEX_HOME` layout, global `AGENTS.md`
policy, `config.toml` features and MCP servers, hooks, task-state directories,
custom read-only agents, and validation without copying personal paths or
secrets into a public repository. Existing laptop `AGENTS.md` and
`config.toml` files are merge targets, not template replacement targets.

### `github-workflows`

`github-workflows` is the repository workflow skill for creating, reviewing,
and standardizing GitHub Actions. Use it for PR and merge CI, release
automation, container publication, merge-bot safety, permissions hardening, and
monorepo-friendly workflow structure.

### `global-context-management`

`global-context-management` keeps complex Codex sessions focused and
recoverable by using durable task-state files, limiting noisy parent-thread
exploration, delegating bounded read-only investigation when the user
explicitly authorizes delegation or enables a local hook delegation policy and
the runtime permits it, choosing targeted helper roles after authorization
instead of requiring the prompt to name them, closing every spawned subagent
handle that is completed or no longer needed before finalizing when close
controls are available, reporting any unavailable or failed cleanup,
and reviewing risk before final answers. Its public skill files stay generic;
local hooks, custom agent config, and task-state files belong under
`$CODEX_HOME`. The hook setup advertises session-scoped task-state paths
without creating missing state files and may suggest bounded same-workspace
prior task-state candidate paths for complex prompts without injecting their
contents; an existing file is meant to be read at task start, resume, or after
compaction when prior context may matter, then updated with concise decisions,
validation status, and next action. This
non-SDLC setup owns `SessionStart` and `UserPromptSubmit` only; Agentic SDLC
skills and guardrails own SDLC phase selection, run state, `PreToolUse`, and
`Stop`.

### `gitignore`

`gitignore` creates or updates a project `.gitignore` with sensible macOS and
VS Code defaults, then extends it for the detected stack.

### `helmchart`

`helmchart` applies Helm chart best practices across metadata, values,
templates, schema, and validation.

### `install-grafana-mcp-for-nebius`

`install-grafana-mcp-for-nebius` installs and configures the official Grafana
MCP server for Codex, refreshes the Nebius-managed Grafana token file, keeps
external Grafana service-account/static-key setup out of the default path, and
guides agents through idempotent Codex MCP registration, datasource discovery,
PromQL-compatible monitoring, Loki, trace-tool checks, and read-only
validation.

### `linter`

`linter` runs a fix-first linting workflow for shell scripts, Markdown, and
Python. Use it when you want syntax checks, `shellcheck`, `markdownlint`, or
`ruff` cleanup applied conservatively.

### `nebius`

`nebius` is the cloud automation skill for Nebius SDK-based workflows,
including IAM bootstrap, object storage, VPC inspection, route analysis, quota
checks, observability, and MK8s GPU/operator decisions.

### `nebius-audit-log`

`nebius-audit-log` is an explicit-only read-only workflow for Nebius Control
Plane Audit Logs queries. It resolves tenant, region, time window, resource or
current subject filters, keeps page size bounded by default, and sanitizes
output unless raw output is explicitly requested.

### `publish-helm`

`publish-helm` publishes an OCI Helm chart end to end from the current project
folder. It requires an explicit release tag and a publish destination from the
caller or project workflow configuration, can create setup assets when missing
or explicitly requested, preps chart release changes on a feature branch
including the `Chart.yaml` version bump, hands off to `create-pr` and
`merge-pr`, tags from the default branch, waits for the tag-triggered workflow,
verifies the pushed chart with `helm pull`, and returns a publish report.

### `publish-image`

`publish-image` publishes a container image end to end from the current project
folder. It collects image and registry inputs without storing secret values,
can create setup assets when missing or explicitly requested, preps changelog
release changes on a feature branch, hands off to `create-pr` and `merge-pr`,
tags from the default branch, waits for the tag-triggered workflow, verifies
the image with `docker buildx imagetools inspect`, and returns a publish
report.

### `publish-release`

`publish-release` publishes a package or application release to GitHub Releases
end to end from the current project folder. It collects package and artifact
inputs, can create setup assets when missing or explicitly requested, preps
release changes on a feature branch, hands off to `create-pr` and `merge-pr`,
tags from the default branch, waits for the tag-triggered workflow, verifies
the GitHub Release and expected assets, and returns a publish report.

### `python-project`

`python-project` scaffolds and hardens Python repositories with reusable modern
defaults such as `pyproject.toml`, setuptools-scm, `src/` layout, Ruff, pytest,
Typer, and Pydantic.

### `review-pr`

`review-pr` is the merge-readiness skill for pull requests. Use it to review a
PR by number, URL, or current branch against its base branch, inspect GitHub
checks and review state, fix safe issues when the branch can be updated safely,
resolve straightforward conflicts when possible, and report remaining blockers.

### `shell-scripting`

`shell-scripting` is the Bash engineering skill for creating, reviewing, and
hardening `.sh` automation.

### `terraform`

`terraform` generates and improves Terraform modules and infrastructure
repositories with reusable structure, state guidance, validation, security
controls, examples, and CI expectations.

## Skills Installer

`install-skills.sh` installs or updates skills into `~/.agents/skills` by
default. It accepts a local source directory or a supported GitHub URL, treats
only folders containing `SKILL.md` as installable skills, keeps reruns
idempotent with `rsync`, skips unmanaged or other-source-owned destinations,
removes stale same-source skills when they disappear from the selected source,
and can remove one installed skill by visible Codex skill name or folder name.
After each install it also lists destination skills that are not present in the
selected source, so renamed or intentionally removed skills are visible and can
be removed with `--remove-skill` when they are not same-source managed.

### Requirements

- `bash`
- `rsync`
- `git` for GitHub sources
- standard POSIX-style utilities for hook installation: `install`, `find`,
  `cmp`, `chmod`, `awk`, `cut`, `sort`, and `mktemp`
- `python3` when using hook registration

### Usage

```bash
./install-skills.sh [source] [destination_dir]
./install-skills.sh --remove-skill <skill_name> [destination_dir]
./install-skills.sh --install-hooks <source_hook_dir> [--register-hooks] [--replace-hooks-json]
./install-skills.sh --install-all-hooks [--register-hooks] [--replace-hooks-json]
./install-skills.sh --help
```

With no arguments, `./install-skills.sh` uses the directory containing the
script as the source and installs every sibling skill folder that contains
`SKILL.md` into the default Codex target, `~/.agents/skills`.
The `--install-hooks` option is deliberately separate from normal skill
installation. It copies hook files from an explicit source hook directory into
`${CODEX_HOME:-$HOME/.codex}/hooks`, stripping `.template` suffixes for
installed files. It copies missing hook files, leaves matching files unchanged,
and stops before replacing any differing existing hook file. Add
`--register-hooks` to merge that bundle's
`hooks.json` or `hooks.json.template` registration manifest into
`${CODEX_HOME:-$HOME/.codex}/hooks.json`.
The `--install-all-hooks` option is also explicit, but discovers every reviewed
hook-only `*/assets/hooks` directory under this source skills folder and syncs
those payload files in one pass. It does not scan mixed `assets/` directories.
With `--register-hooks`, it also merges each discovered bundle's registration
manifest while preserving existing hook entries. Add `--replace-hooks-json`
only when you intentionally want to back up and replace `hooks.json` with a
clean file built from the selected source manifests. Hook install modes are
idempotent: unchanged files are not recopied, differing existing hook files are
left for manual review, registration appends only missing source entries by
default, refuses duplicate Python hook files within the same hook event, and
any extra installed hook files or hook registrations are reported for review
instead of removed automatically.

### Supported Sources

- Local directory path. The default source is the script directory, and a local
  source can be either a multi-skill folder or a single skill folder containing
  `SKILL.md`.
- GitHub repository URL:
  `https://github.com/<owner>/<repo>`
- GitHub tree URL:
  `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>`

### Examples

```bash
# Install all skills from this folder into the default destination
./install-skills.sh

# Install from an explicit local source directory
./install-skills.sh ~/test

# Install from a GitHub repository root
./install-skills.sh "https://github.com/openai/skills"

# Install from a nested GitHub skills folder
./install-skills.sh "https://github.com/openai/skills/tree/main/skills"

# Install one specific skill from a nested GitHub path
./install-skills.sh \
  "https://github.com/openai/skills/tree/main/skills/.curated/openai-docs"

# Install to a custom destination
./install-skills.sh \
  "https://github.com/openai/skills/tree/main/skills" \
  "~/custom-skills"

# Remove an installed skill by its visible Codex skill name
./install-skills.sh --remove-skill nebius

# Remove an installed skill by its folder name
./install-skills.sh --remove-skill vendor-nebius

# Remove from a custom destination
./install-skills.sh --remove-skill vendor-nebius "~/custom-skills"

# Copy optional Agentic SDLC hooks into the default local Codex home
./install-skills.sh --install-hooks sdlc-start/assets/hooks

# Copy and register global context-management hooks
./install-skills.sh --install-hooks config-codex/assets/hooks --register-hooks

# Copy and register every reviewed hook-only bundle
./install-skills.sh --install-all-hooks --register-hooks

# Copy all reviewed hook bundles and replace hooks.json with only those entries
./install-skills.sh --install-all-hooks --register-hooks --replace-hooks-json

# Copy hooks into a non-default Codex home
CODEX_HOME=~/custom-codex ./install-skills.sh --install-all-hooks --register-hooks
```

### Notes

- If newly installed skills are not visible, run `Developer: Restart Extension
  Host` in VS Code.
- A valid skill folder must contain `SKILL.md`.
- Existing unmanaged folders in the destination are never overwritten.
- If an install prints `Skip (existing unmanaged directory): <skill_name>`,
  remove the destination copy and reinstall it from the current source:

  ```bash
  ./install-skills.sh --remove-skill <skill_name>
  ./install-skills.sh
  ```

- If a skill exists but belongs to another source, it is skipped.
- Skills previously installed from the same source are removed when they no
  longer exist in that source, so source-owned renames converge on reinstall.
- Other destination skills that are not present in the selected source are
  listed at the end with a `--remove-skill` hint.
- `--remove-skill` accepts either the exact `name:` from `SKILL.md` or the
  installed folder name.
- `--remove-skill <skill_name>` without an explicit destination removes from
  the default Codex skills target, `~/.agents/skills`.
- If you installed into a custom destination, pass that destination to
  `--remove-skill`.
- `--remove-skill` removes the destination skill folder and its local manifest
  entries.
- Reinstalling from a source that still contains a removed skill will add it
  back.
- Stale skill cleanup only applies to skills previously installed from the same
  source.
- `--install-hooks <source_hook_dir>` is opt-in because hooks are local runtime
  guardrails, not skills. Use a hook-only source directory such as
  `sdlc-start/assets/hooks` or `config-codex/assets/hooks`. Without
  `--register-hooks`, this only syncs files under
  `${CODEX_HOME:-$HOME/.codex}/hooks`, and it stops before replacing any
  differing existing hook file.
- `--install-all-hooks` discovers only skill-owned hook-only directories named
  `*/assets/hooks` under this source folder, checks for conflicting installed
  file names, and syncs all reviewed hook bundles into
  `${CODEX_HOME:-$HOME/.codex}/hooks` without overwriting differing existing
  hook files.
- `--register-hooks` can be combined with either hook-install mode. It looks
  for `hooks.json` or `hooks.json.template` in the hook directory or its parent,
  validates the source and destination JSON, backs up an existing
  `${CODEX_HOME:-$HOME/.codex}/hooks.json` before changing it, preserves
  existing entries, and appends only missing source entries. It refuses to
  create or preserve multiple registrations for the same hook event and Python
  hook filename, such as two `Stop` entries pointing at
  `stop_sdlc_continue.py`.
- `agent-nebius-auth` keeps hook installation canonical: setup writes the
  project selector under `~/.nebius`, while the root installer syncs hook files
  and `hooks.json` only. It does not migrate inline `config.toml` hook entries;
  it rejects stale inline agent-nebius-auth entries before copying hooks or
  writing `hooks.json`.
- `--replace-hooks-json` can be combined with `--register-hooks` to replace
  `${CODEX_HOME:-$HOME/.codex}/hooks.json` with a clean file built from the
  selected source manifest or manifests. This removes hand-written and stale
  registrations that are not in the selected source. Use
  `--install-all-hooks --register-hooks --replace-hooks-json` for a clean file
  containing every reviewed hook bundle under this source folder.
- Hook install modes report extra files under
  `${CODEX_HOME:-$HOME/.codex}/hooks` and extra `hooks.json` registrations that
  are not present in the selected source manifests. These reports are advisory:
  review the entries and remove obsolete files or JSON entries manually.
- Hook registration does not trust hooks. Restart Codex and review/trust new or
  changed hook entries in `/hooks`.
