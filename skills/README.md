# Skills

This folder contains public, reusable Codex skills for common engineering
workflows. Each skill lives in its own folder and is discovered by the presence
of `SKILL.md`.

This root README is the concise catalog and install guide. Most skill folders
also have a local `README.md` that explains architecture, core concepts,
workflow, and important files. `SKILL.md` remains the concise runtime
instruction file Codex loads when the skill is used; longer command cookbooks,
rubrics, standards, and templates live under `references/` or `assets/` and
are loaded on demand.

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
| `brainstorm` | Implicit allowed | Explore ideas in chat with relevant source-ranked project, repo, skill, internal, vendor, bounded research for unresolved source conflicts, and advisory design-skill context before implementation. |
| `code-review` | Implicit allowed | Neutral findings-first review of local diffs, local branches, changed files, modules, repository areas, or patches for bugs, tests, reliability, maintainability, and structural simplification. |
| `create-learning-course` | Explicit only | Create public-safe learning courses, course workspaces, syllabi, lessons, exercises, glossaries, and publication review checkpoints. |
| `global-context-management` | Implicit allowed | Keep complex Codex tasks focused with durable task state, concise parent-thread context, targeted read-only subagents when the prompt or local hook policy request authorizes delegation, focused validation, and final risk review. |
| `research` | Implicit allowed | Senior-engineer technical due diligence with internal Slack/Confluence context first where relevant, MCP fallback for internal systems, vendor verification, alternatives, and recommendations. |

### Local Setup and Information

| Skill | Invocation | Description |
| --- | --- | --- |
| `agent-nebius-auth-diagnose` | Implicit allowed | Discover the current-session Nebius project and diagnose agent-auth or hook failures without mutation. |
| `agent-nebius-auth-setup` | Explicit only | Converge one service account, one exact-permit group, project-bound auth, or a bounded local-repair lease. |
| `sdlc-workflow-test` | Explicit only | Run the unchanged lightweight SDLC verifier or explicitly create, keep, resume, and destroy one owned real three-tier Docker application with computer-use GUI UAT. |
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
| `worktree` | Explicit only | Create and manage one project-scoped full-repository worktree from `origin/main`, with serialized publication, nested task ownership, and proof-gated cleanup. |

### Project Engineering

| Skill | Invocation | Description |
| --- | --- | --- |
| `app-stack` | Implicit allowed | Select, review, simplify, modernize, and coordinate implementation of the smallest justified application technology stack across application archetypes. |
| `apply-security` | Implicit allowed | Advise on, review, and safely remediate security issues across design, implementation, infrastructure, deployment, Helm, Kubernetes, Terraform, CI/CD, shell, and application code. |
| `design` | Implicit allowed | Design software features, applications, components, APIs, data flows, and vertical slices before implementation, using `research`, `app-stack`, and `system-design-rules` where relevant before the `/plan` handoff. |
| `github-workflows` | Implicit allowed | Create, review, or standardize GitHub Actions for PR/merge CI, merge automation, reusable workflows, permissions, and release/image YAML. |
| `gitignore` | Implicit allowed | Create or update stack-aware `.gitignore` files with sensible macOS, VS Code, and detected language/tool defaults. |
| `helmchart` | Implicit allowed | Create, review, harden, refactor, lint, template, or standardize Helm charts and chart CI. |
| `linter` | Implicit allowed | Lint and conservatively auto-fix shell, Markdown, and Python files with tools such as `shellcheck`, `markdownlint`, and Ruff. |
| `nebius` | Implicit allowed | Automate Nebius SDK/cloud workflows for IAM, object storage, VPC, quota, MK8s readiness, GPU/operator decisions, and observability wiring. |
| `nebius-audit-log` | Explicit only | Query Nebius Control Plane Audit Logs by resource or current subject with bounded, sanitized read-only CLI output. |
| `python-project` | Implicit allowed | Scaffold or harden Python projects with modern packaging, `src/` layout, Ruff, pytest, Typer, Pydantic, services, APIs, and CI. |
| `shell-scripting` | Implicit allowed | Create, refactor, or review Bash automation with strict mode, safe argument parsing, idempotency, and readable CLI output. |
| `system-design-rules` | Implicit allowed | Evaluate system designs, ADRs, architecture options, APIs, data ownership, reliability, security, observability, scale, cost, and team boundaries with a practical design checklist. |
| `task-implementer` | Explicit only | Coordinate durable dependency waves through internal worktrees, including safe nesting under a `worktree`-managed outer branch. |
| `task-implementer-test` | Explicit only | Run lightweight Task Implementer verification or own one replaceable disposable multi-tier live fixture. |
| `terraform` | Implicit allowed | Scaffold, standardize, or improve Terraform repositories and modules with state guidance, validation, security controls, examples, and CI. |
| `troubleshoot` | Implicit allowed | Causally investigate and repair difficult code, shell, installed-software, service, container, network, storage, and infrastructure failures with evidence preservation, competing hypotheses, bounded live safety, and layered verification. |

### Agentic SDLC Workflow

All `sdlc-*` skills are explicit-only. The phase skills run through the
Agentic SDLC workflow, starting with
`$sdlc-start workspace init [project-folder]` and then
`$sdlc-start run <prompt-path-or-unique-filename>`; the external
`sdlc-workflow-test` verifier is not a phase.

| Skill | Invocation | Description |
| --- | --- | --- |
| `sdlc-align-specs` | Explicit only | Check SDLC requirements, design, plans, tests, implementation, documentation, end-to-end slice evidence, and other evidence for consistency. |
| `sdlc-auto-steering` | Explicit only | Refresh private active-run steering by recording mid-run user prompts, classifying them, and deriving compact reminders before the next SDLC phase. |
| `sdlc-classify-failure` | Explicit only | Classify failed SDLC phases and route the loop to the earliest responsible phase. |
| `sdlc-commit` | Explicit only | Seal final integration changes, ff-only promote the exact verified tip to the unchanged project branch, and non-force-clean integration resources; never pushes. |
| `sdlc-create-design` | Explicit only | Create or update `docs/design.md` from requirements, gathered context, and codebase evidence, preserving stable feature IDs, vertical feature flow, and implementation-ready design boundaries. |
| `sdlc-create-plan` | Explicit only | Create a locked private local task graph for one ready feature, including dependencies, write claims, conflict domains, and validation boundaries. |
| `sdlc-create-requirements` | Explicit only | Create or update `docs/requirements.md` from user prompts, tickets, stories, change requests, and optional safe live experiment environment details while preserving stable requirement IDs. |
| `sdlc-evaluate` | Explicit only | Evaluate whether the current feature and any planned end-to-end slice solve the real-world requirement using acceptance criteria, the right harness, and any confirmed safe live experiment environment. |
| `sdlc-gather-context` | Explicit only | Build compact feature context packs from product, vendor, internal, codebase, layer-boundary, and test sources. |
| `sdlc-gui-test` | Explicit only | Control and evaluate browser UI behavior against SDLC acceptance criteria with screenshots or accessibility snapshots when available. |
| `sdlc-implement-plan` | Explicit only | Coordinate dependency waves with one fresh agent, branch, and private worktree per safe task, ordered integration, combined evidence, and non-force cleanup. |
| `sdlc-merge-pr` | Explicit only | Merge a specific Agentic SDLC pull request only after explicit user request and final readiness checks. |
| `sdlc-prepare-execution` | Explicit only | Prepare or resume the persistent feature integration worktree and deterministic task waves after plan lock and before TDD. |
| `sdlc-start` | Explicit only | Initialize a private prompt workspace, then start, resume, or steer a prompt-bound Agentic SDLC run and choose its next phase. |
| `sdlc-tdd` | Explicit only | Convert acceptance criteria, design success criteria, and any planned end-to-end slice into failing or already-green tests before implementation. |
| `sdlc-tui-test` | Explicit only | Control and evaluate terminal, CLI wizard, or TUI flows with transcript and exit-code evidence. |
| `sdlc-update-documents` | Explicit only | Update project-facing README, changelog, usage docs, examples, or generated docs from implemented and evaluated SDLC evidence without editing requirements or design. |
| `sdlc-uat-tests` | Explicit only | Run product-level user acceptance testing across the whole system, using any confirmed safe live experiment environment, before PR creation. |
| `sdlc-unit-tests` | Explicit only | Run behavior, regression, integration, component, contract, or mock-based tests for the current feature and planned slice. |
| `sdlc-validate-codes` | Explicit only | Run build, parse, lint, type, import, dependency, configuration, and locked-slice boundary validation for the current feature, then use `code-review` as a review-only quality gate. |

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
`agent-nebius-auth-setup` is explicit-only. Direct invocation authorizes its
bounded canonical convergence, so it does not add a second confirmation prompt
or require a plan digest. Read-only implicit triage belongs to
`agent-nebius-auth-diagnose`.

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

$worktree Create an isolated worktree from origin/main for the current monorepo project to fix trigger validation.

$worktree create-pr Open or reuse the PR for this managed worktree, then leave cleanup for a separate remove action after merge.

$worktree remove project-fix-trigger-validation-a7c2f9 after verifying its exact PR head was merged.

$review-pr Review PR #110 against the base branch, fix safe issues on the branch, and tell me whether it is ready to merge.

$review-pr Review https://github.com/example-org/example-repo/pull/42, resolve straightforward conflicts against main if the branch is writable, and report remaining blockers.

$merge-pr Merge PR #110 with squash after verifying checks, reviews, mergeability, and the head SHA without using admin bypass.

$publish-image --mode complete --tag 1.2.3 --image-name ghcr.io/example-org/example-app prep, PR, merge, tag, wait for CI, verify the image digest, and report the published artifact.

$sdlc-workflow-test Verify the Agentic SDLC workflow against docs/agentic-sdlc-design.md and write a safe report.

$sdlc-workflow-test --create --keep

$sdlc-workflow-test --resume

$sdlc-workflow-test --destroy

$sdlc-start workspace init services/example-app

$sdlc-start run <prompt-path-or-unique-filename>

$align-skill Review and standardize skills/foo against the canonical skill structure and official vendor docs.

$align-skill Harden this scaffolded skill folder into a safe, secure, fast Codex skill, then validate it.

$brainstorm Explore this architecture idea, gather the relevant project docs, related skills, internal context, and official vendor docs, and challenge weak assumptions before we implement anything.

$create-learning-course Create a public-safe course workspace for engineers learning Kubernetes networking, with mission, syllabus, sources, HTML lessons, exercises, glossary, and publication review.

$research Research Kubernetes Gateway API, search internal Slack and Confluence context first if relevant, explain how it works internally, identify limitations and alternatives, and recommend when we should or should not use it.

$design Design this new feature before implementation: read the requirements, inspect the existing code and docs, route unfamiliar topic and technology research through $research, use $app-stack for any undecided application-layer technology choices, apply $system-design-rules to the non-trivial design decisions, compare options, and create a /plan handoff.

$app-stack Select the smallest justified stack for this application, mark optional components with their adoption triggers, and coordinate implementation through matching specialist skills.

$code-review Review the current local branch for bugs, regressions, test gaps, reliability risks, maintainability blockers, and missed structural simplifications.

$system-design-rules Review this ADR against the system design checklist, compare the trade-offs, and identify missing reliability, data, security, observability, cost, and ownership decisions.

$task-implementer workspace init services/nebius-cxcli

$task-implementer run <prompt-path-or-unique-filename>

$task-implementer-test

$task-implementer-test --create --keep

$task-implementer-test --destroy

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
`create-pr`, `merge-pr`, `review-pr`, and `worktree`, that means the current
directory is inside a Git repository. For remote-backed flows such as
`commit-push`, `create-pr`, `merge-pr`, `review-pr`, `worktree`, and the
`publish-*` complete flows, that
also means:

- the repository has an `origin` remote
- the branch state allows the requested operation

For GitHub CLI backed flows such as `create-pr`, `merge-pr`, `review-pr`,
`worktree remove`, and the `publish-*` complete flows, that also means:

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
learning capture in local skill source materials need to be aligned. Before it
claims a target skill is aligned, it applies `code-review` in review-only mode
and `apply-security` in advisory or scan mode to the target skill scope, and it
reports fixed, deferred, skipped, incomplete, or blocking findings.

### `brainstorm`

`brainstorm` supports evidence-first, chat-only ideation before implementation.
It restates the topic, builds a compact source plan with a relevance reason for
each source, gathers context from the current project folder first, then
sibling repo folders, related skills, internal Confluence/Slack/Jira sources
when available, and official vendor docs only when those sources can answer the
question, resolve the challenge, close a named gap, or change the
recommendation. It separates facts from hypotheses, challenges weak
assumptions, compares options, consults `design` and `system-design-rules` for
major decisions when those skills are installed and accessible, uses bounded
`research` only for unresolved recommendation-changing source conflicts, and
stops short of editing files, creating tickets, sending messages, or mutating
external systems. If the user pivots to execution, it should
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
requirements. For organization-tied questions, it searches internal Slack and
Confluence context through available connectors first, uses MCP or app-tool
access for internal systems when connectors are unavailable, then verifies
technical claims against official vendor documentation and authoritative
external sources. The output cross-checks internal and external evidence and
labels organization-specific guidance, vendor-documented behavior, general
industry practice, and unverified claims. Use `brainstorm` for open-ended
ideation, `design` for solution design and `/plan` handoff, and
`system-design-rules` for checklist review of an existing proposal.

### `design`

`design` turns requirements, existing-system evidence, and `research`-backed
topic, requirement, and technology due diligence into a concrete software
design before implementation. It follows a phased workflow: understand
requirements, understand the existing system or greenfield context, route
missing knowledge through `research` when available, route undecided
application-stack or layer technology choices through `app-stack`, design the
solution, apply `system-design-rules` to non-trivial solution decisions,
evaluate alternatives, define vertical end-to-end slices for serial multi-layer
applications, and create a Codex `/plan` handoff. Use it for new features,
major changes, APIs, data flows, integrations, and new applications when the
user wants a practical design and implementation-ready plan, not immediate
coding. Use `brainstorm` for open-ended ideation, `system-design-rules` for
checklist review of an existing proposal, `app-stack` directly for a
stack-selection-only request, and `sdlc-create-design` for Agentic SDLC-owned
`docs/design.md`.

### `app-stack`

`app-stack` selects, reviews, simplifies, modernizes, and coordinates
implementation of application technology stacks. It starts from the product
journey, application archetype, quality attributes, team skills, deployment
constraints, data ownership, and operational capacity before choosing
products. Every component is classified as required, conditional, deferred, or
rejected, with a rationale and revisit trigger, so queues, caches, workflow
engines, event streams, Kubernetes, and service boundaries are added only for
concrete requirements.

The skill is universal in selection scope while keeping Python, FastAPI,
PostgreSQL, SQLAlchemy, and Alembic as one opinionated general-web profile. It
remains read-only for advisory requests. When implementation is requested, it
owns cross-layer sequencing and coordinates the narrow installed specialist
skills that match the selected stack instead of duplicating their workflows.
Use `research` for deep technology due diligence, `design` for a complete
solution design, and stack-specific skills directly when the stack is already
fixed and no selection decision remains. When `design` delegates a scoped stack
decision, `app-stack` returns it to the active design workflow instead of
starting a recursive handoff.

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

`task-implementer` coordinates complex brownfield requests through deterministic dependency waves.
It keeps durable prompts and orchestration evidence under
`${CODEX_HOME:-$HOME/.codex}/task-implementer/projects/`, outside Git, with one
editable Markdown file per independent ask. A generated VS Code workspace puts
`CODE` first and `PROMPTS` second so source and historical asks are visible
together without making Codex depend on multi-root behavior.

`workspace init [project-folder]` defaults to the exact current directory. It
creates or verifies the private workspace, creates one starter prompt only when
none exists, opens VS Code when available, and is safe to repeat without
changing prompts or history. One `run <prompt-path-or-unique-filename>` validates
and snapshots exact prompt bytes, creates or reconciles the internal task queue,
locks dependencies, exact/prefix write claims, conflict domains, validation,
and done criteria, then coordinates every wave until completion or a blocker.

Users steer the workflow by editing the same prompt—preferably appending to its
optional `## Steering` section—and running the same command. There is no public
`steer` command or user-supplied ID. Steering may safely recompute a merely
planned wave. Once worktrees or assignments exist, it queues without changing
the immutable active wave and is reconciled before promotion or at the next
wave boundary.

Requirements are normalized before tasks into stable `TI-REQ-nnn` records, and
each selected task receives a `TI-DES-nnn` design. The coordinator creates or
updates only marked regions in project
`docs/requirements.md` and `docs/design.md`, byte-preserving generic content
outside them. Agentic SDLC ownership or malformed/unsafe managed state fails
closed. Workers never edit these shared specifications concurrently.

Parallel-capable tasks receive unique branches and full-repository linked
worktrees under the private task-implementer root. For monorepo scopes, workers
operate from the scope path inside those full checkouts. Native workers dispatch
up to capacity; fresh sequential `codex exec` workers provide the same isolation
when native subagents are unavailable. Each worker implements one locked task,
validates, runs `code-review`, and creates exactly one direct-child `$commit`.
Worker-assignment v7 also carries immutable default guardrails plus exact
helper/workspace-manifest paths for the first transition: unless the exact
assignment authorizes it, a worker stays inside its worktree/private
state; required installed skill instructions/helpers and standard executables
are read/execute-only. It does not modify installed files, intentionally write
unrelated paths, or access network, credentials, external services, or live
runtimes. Applicable prompt and repository constraints, implementation steps,
and end-to-end validation are repeated in each self-contained assignment.
Workers heartbeat every 30 seconds. Dependency-free `standard` tasks warn/stop
at 240/300 seconds without claimed-file progress; dependent `integration` tasks
warn/stop at 360/420 seconds. Heartbeats become hard-stale at 240 seconds;
workers that exceed their
immutable total budget are interrupted independently.
Workers receive fresh assignment-only context and must reach `task-start`
within 60 seconds after the coordinator arms an available worker slot. Queued
assignments remain unarmed and do not consume the deadline.
The worker makes `task-start` its first private transition after immediate
Git/cwd verification, passing the embedded digest unchanged through the exact
embedded paths. The helper performs authoritative canonical digest validation,
so the worker never guesses JSON serialization. It then reads the incoming
handoff and performs deeper preflight.
At the profile warning, the coordinator demands an edit or blocker; background
and autonomous heartbeat loops are forbidden. Workers rely on the assignment
and incoming handoff instead of rereading the full prompt or coordinator state.
Worker start is single-use, and only in-claim mutations count as liveness
progress.

The coordinator verifies worker commits and changed paths, merges task branches
into a temporary integration branch in stable task-ID order, runs combined
validation and review, reconciles steering, and advances the unchanged primary
branch only with `git merge --ff-only`. Tasks become done after promotion.
Clean reachable worktrees and branches are removed without force; any failure
retains exact recovery resources and leaves the project branch unchanged.

When the project checkout is itself managed by `worktree`, the exact outer
branch `HEAD` is the task base and sole promotion target. A private lease keeps
all worker and integration branches internal, blocks outer push/PR/removal,
and remains through per-wave cleanup plus final changed-surface `align`. Only a
clean final promoted head with no internal resources can release it.

The helper uses only the Python standard library, applies private POSIX modes,
rejects path and symlink escapes, journals Git mutations, and never prints
prompt bodies. Every v1 execution record fails with
`WORKFLOW_UPGRADE_REQUIRED`; no legacy execution schema is readable. The Skill
is explicit-only. Use `global-context-management` for general context hygiene,
`$sdlc-start run <prompt-path-or-unique-filename>` for Agentic SDLC, and
`align` for final alignment.

### `task-implementer-test`

`task-implementer-test` verifies Task Implementer without using a real user
project. With no flags it runs the current explicit-only and exact two-command
contract checks plus the local temporary-fixture workspace, specification,
scheduler, Git-wave, outer-worktree, verifier-helper, lifecycle, reporting, and
semantic suites. It never starts Docker, dispatches implementation workers, or
creates a persistent application.

The opt-in `--create` mode first replaces any prior exactly owned verifier
generation, then exercises the real Task Implementer public interface on a
seeded remote-free brownfield task board. The target is a browser frontend,
HTTP API, and PostgreSQL stack with disjoint first-wave tier ownership and a
dependent integration/runtime task. The verifier checks worker isolation,
reviewed commits, ordered integration, ff-only promotion, final alignment,
frontend/API/database semantics, and restart persistence before writing a
sanitized report and cleaning the fixture. PASS is gated by a generation-bound
semantic transition over canonical Task Implementer state, Git identity, the
helper-collected application artifact, and an unchanged post-completion prompt
invocation.
The live verifier checks worker liveness every 30 seconds, stops rather than
recovering a stalled disposable worker, and proceeds directly from a validated,
cleaned Task Implementer workspace to runtime evidence and reporting.
Its generation-fenced stage ledger records deterministic checks, individual
tier workers, wave integration, finalization, runtime, semantic validation,
reporting, and cleanup as PASS/PARTIAL/FAIL/NOT_RUN. The preserved report shows
the complete stage matrix, exact bounded failure analysis, downstream stages
that did not run, and a minimum next action even when normal report creation
fails.

Before Docker sees generated configuration, the verifier accepts only the
fixture's narrow Compose subset and rejects external includes, extends, label
files, privileged build options, and external build/cache inputs. Runtime
service networks use the verifier's long-form object-map syntax rather than
Compose list shorthand. Runtime
collection stays under one generation lock, and built-image cleanup requires
both exact generation and verifier project labels.

`--create --keep` retains the one current exact generation for inspection.
`--destroy` later removes only its ownership-checked project, isolated Task
Implementer state, raw evidence, and runtime resources while preserving reports
and lifecycle history. Every create is replace-on-create. Symlink, marker,
remote, external-worktree, generation, or Docker ambiguity blocks replacement
rather than creating a second instance. Explicit destroy or replacement also
removes inspection edits inside the exact disposable owned run.

### `sdlc-workflow-test`

`sdlc-workflow-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks `docs/agentic-sdlc-design.md`, required source-installed
skill parity, explicit-only invocation policy, prompt/execution/worktree/
steering regressions, composed managed-outer lease behavior, and disposable
hook fixtures. Optional private live-results evidence covers the golden path,
idempotency, change requests, failure routing, auto-steering, documentation,
and continuation, plus one explicit evidence row for each of the 20 Agentic
SDLC skills. Live PASS requires a real selected-scope commit, lane-specific
private evidence, the exact complete skill matrix, and clean in-scope paths
across every commit in the supplied history. It writes under
`~/.codex/sdlc-verification/` and must not change real projects, installed
skills, hooks, hook trust, or agent configuration.

The rename is an intentional ownership hard cut. Destroy retained live
verifier environments before installing it; the renamed skill does not read or
clean old-format ownership markers, labels, or Compose names.

The no-flag invocation remains the lightweight resource-validator verifier and
does not touch Docker or a browser. Every explicit `--create` first safely
destroys the previous active exactly owned environment, then builds and tests a
fresh browser GUI, Django/Gunicorn web/API server, and PostgreSQL database
through the normal two-command Agentic SDLC workflow. It launches one fresh
verifier-owned Chrome process group with a private profile and exact marker,
never an existing Chrome instance. It uses two owned Docker Compose
containers, a dynamically assigned loopback web port, an internal-only database
endpoint, semantic cross-layer evidence, and computer-use GUI UAT correlated
with API and database observations. By default it writes a complete report and
then removes every exact owned live resource, even after failure. Browser
cleanup revalidates and signals only the recorded process group; identity
ambiguity fails closed as `CLEANUP_FAILED` before Docker mutation.

Initial Computer Use capture proves capability discovery only. The live profile
repeats a fresh capture immediately before GUI evaluation and UAT with an
unlocked host unless locked Computer Use is explicitly enabled for the session,
and a visible foreground current-Space browser window. A
pre-navigation visibility failure is reported as `ENVIRONMENT_DEFECT`; a hung
or non-responsive shared Computer Use service stops further calls and requires
separately authorized recovery while the owned application is preserved.

`--create --keep` performs the same replacement and then retains the new owned
project, private state/evidence, running services, database volume, built image,
and dedicated Chrome instance/profile. A later
`--resume` revalidates and continues that application only after a failed or
partial kept run. A later `--destroy` closes only that exact verifier-owned
Chrome process group and removes resources whose canonical identities and two
ownership labels match the private lifecycle, while retaining sanitized reports
and lifecycle history. Existing Chrome processes are never targets. One
active application is allowed per verification root; cleanup ambiguity blocks
replacement rather than starting a second stack. Cleanup includes resources
discovered by the exact verification-ID and Compose-project labels even when a
prior interruption prevented inventory capture. Name/ID aliases are
canonicalized and deduplicated, and cleanup retries preserve their cumulative
removed/already-absent ledger. Later helper mutations and
Compose actions are generation-fenced so a superseded invocation cannot
continue against the replacement. Repeated destroy returns `ALREADY_DESTROYED`.

### `agent-nebius-auth-diagnose` and `agent-nebius-auth-setup`

`agent-nebius-auth-diagnose` is the implicit, read-only runtime entry point. It
discovers the project from current-session evidence, treats persistent memory
only as a corroborated hint, and never infers authority from active profiles,
credential filenames, cwd, legacy default selectors, or unrelated task state.

Correctable command-policy denials are fixed and retried by the agent without
running setup: add the exact leading selector, remove a conflicting
explicit profile or managed-auth assignment, or replace a token-printing
command with the normal operation or redirected verification form.

`agent-nebius-auth-setup` is explicit-only. Invoking it directly authorizes one
bounded convergence, without a second prompt or confirmation digest. It locks
first, validates the current non-agent administrative profile by minting a
human-user token to discarded stdout, resolves authoritative project metadata
once, and performs one convergence pass. Human-profile authentication owns IAM
bootstrap and authorized-key creation until the agent account is ready; the
token is never printed or persisted. `--dry-run` remains available for an
explicitly requested read-only preview.

The selected project owns the `codex-agent-sa` service account. One
deterministic group is parented by the authoritative tenant and contains
exactly two permits: `admin` on the selected project and `viewer` on that
tenant. Its name depends only on the project ID hash, so project renames cannot
create another canonical group. A tenant-created group can hold both scopes, so
setup no longer creates a separate quota-specific group. Tenant `viewer` enables read-only quota
allowance listing but is broader than quota access alone. Setup rejects extra
or duplicate permits on its managed group, rejects extra or duplicate members,
never grants tenant write access, and does not automatically delete old groups
or external grants.

Existing credentials normally must resolve to the expected account and project
before mutation. If the credential's service-account ID instead returns the
provider-classified RPC/API `NotFound`, explicit setup may bootstrap or reuse a
distinct fixed account through the human profile, reconcile the exact IAM shape,
generate one checked authorized-key credential, create one mode-`0600` backup,
atomically replace the stale file, and rebind the profile. Permission,
authentication, transient, parse, generic "not found", and unclassified lookup
failures remain non-recoverable. If a matching current credential cannot mint a
token, setup backs it up and replaces it at most once in the same explicit
invocation only after a classified credential-authentication failure. Profile
write errors and transient or unclassified token failures do not rotate keys. A
second failure stops without another key, revocation, or prompt. Runtime
verification also binds the profile-reported service-account identity and the
project lookup for the fixed `codex-agent-sa` to the canonical credential.

When explicitly requested after setup and basic project-access verification
succeed, a 12-hour repair lease (24-hour maximum) may authorize only fd-safe
mode-`0600` correction of the exact fingerprinted credential and rebuilding
its exact local profile. No extra confirmation digest is required. The
lease is private same-user workflow state, not an unforgeable security token.
It never authorizes key generation/rotation, IAM, identity, or hook changes and
fails closed on path, owner, schema, fingerprint, identity, action, or expiry
drift.

At runtime, every Nebius-sensitive Bash command starts with
`CODEX_NEBIUS_PROJECT_ID=<project-id> <command>`. The hook strips and validates
that one leading selector and injects the matching renewable profile,
project, credential-file, and token-helper context for any selected executable.
It never falls back to inherited auth, a global default file, or a single
credential filename, and it clears ambient bearer variables instead of
injecting a universal expiring token. Install or refresh it only when the user
explicitly requests that separate action. Use
`./install-skills.sh --install-hooks agent-nebius-auth-setup/assets/hooks --register-hooks`.
Normal CLI/profile and supported SDK credential paths own renewal. Raw Bash,
Python, or API children use
`python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command>`; an explicitly
idempotent adapter may use `retry-idempotent` for one refresh and retry only
after a real 401/`UNAUTHENTICATED` mapped to status `77`. Already-running
processes need provider-native renewal, bounded helper calls, or restart.

Read-only runtime diagnosis uses `agent-nebius-auth-setup.sh verify` and never
requires a human/admin profile. It verifies token mint, project access,
authoritative project-to-tenant ancestry, and one tenant quota-allowance list
call. Exact IAM planning remains separate and returns `blocked-admin-auth` when
the required non-agent administrative profile cannot authenticate
non-interactively.

### Agentic SDLC Skills

The Agentic SDLC skills implement a skill-driven state machine for turning user
ideas into requirements, design, locked local plans, test-first implementation,
validation, evaluation, local commits, UAT, PR creation or reuse, PR review,
and explicit final merge.
Strictly SDLC-only skills use the `sdlc-` prefix, with the coordinator named
`sdlc-start`, so tool discovery does not confuse workflow phases such as
`sdlc-commit` with ordinary Git commands or general-purpose engineering skills.
All `sdlc-*` skills set `allow_implicit_invocation: false`. Except for the
external `sdlc-workflow-test` verifier, initialize and run the workflow through
exactly `$sdlc-start workspace init [project-folder]` and
`$sdlc-start run <prompt-path-or-unique-filename>`, then let the coordinator
record the next recommended phase in local run state. Editing the same managed
prompt and repeating `run` is the steering path; bare `$sdlc-start` is not a
resume interface.
The committed product truth is `docs/requirements.md` and `docs/design.md`;
private run state, plans, evidence, screenshots, transcripts, and steering live
under `~/.codex/sdlc-runs/<project-id>/<run-id>/` and must not be committed.
Each active feature also has schema-v4 execution state and private worktrees
there. After plan lock, `sdlc-prepare-execution` creates a persistent
integration branch/worktree and enforces the initialized monorepo folder as the
claim and worker-cwd boundary. `sdlc-implement-plan` runs safe tasks in enforced
capacity batches inside dependency waves, using one fresh native agent or
sequential ephemeral `codex exec` fallback per task and immutable direct-
predecessor handoffs, retains worker and ordered
merge commits, and cleans only proven reachable resources without force. The
project branch stays unchanged until `sdlc-commit` seals the final integration
tip and promotes it with `git merge --ff-only`.
Project-level managed prompts and immutable run revisions also remain under
`~/.codex/sdlc-runs/<project-id>/`. `STEERING.md` is the active-run inbox and
steering ledger for accepted prompt revisions, while
`steering/auto-steering.json` stores machine-readable
dispositions and compact reminders. Requirements or design changes captured in
steering still route through the owning product-truth skills before
implementation treats them as true.
`docs/requirements.md` may also record an optional Live Experiment Environment
so later evaluation and UAT can use a confirmed non-production or disposable
target with safe connection, allowed-action, reset, and evidence rules.
Optional global PreToolUse and Stop hooks can enforce SDLC invariants from that
local state. The Stop hook repeats the prompt-bound `sdlc-start run` command
rather than routing directly into phase skills.
Sensitive Git actions use
short-lived local authorization files under the active run's `permissions/`
directory; the skills create those files only immediately before the guarded
action. Registered integration and worker worktrees remain inside hook policy
even outside the original checkout, with exact Git identity and action-scoped
authorization checks for sensitive raw Git operations.
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
  prompts, tickets, approved change requests, and optional safe live experiment
  environment details while preserving stable `REQ-*` IDs.
- `sdlc-start`: initializes the private prompt workspace, accepts immutable
  prompt revisions, coordinates the active SDLC run, reads steering and local
  checkpoints, and chooses one next skill without duplicating history. At run
  start, it encourages safe live experiment environment capture through
  requirements.
- `sdlc-gather-context`: builds compact feature context packs from official docs,
  internal sources, code, tests, and layer-boundary evidence when a vertical
  slice may apply.
- `sdlc-create-design`: creates or updates `docs/design.md`, maps requirements to
  stable `FEAT-*` blocks, records selected and rejected design options, and
  defines vertical feature flow, layer map, implementation, validation, test,
  evaluation, rollout, and rollback boundaries.
- `sdlc-auto-steering`: refreshes private active-run steering, records every
  mid-run user prompt safely, classifies entries, derives compact reminders,
  and routes requirements, design, docs, or human-input changes back through
  `sdlc-start`.
- `sdlc-create-plan`: creates locked private local execution plans for one feature,
  preserving the end-to-end slice and defining stable dependency-safe task
  records.
- `sdlc-prepare-execution`: validates the task graph and prepares the persistent
  feature integration worktree and deterministic waves before TDD.
- `sdlc-tdd`: writes or maps tests in the integration worktree before
  implementation, including planned slice contracts and cross-layer validation
  targets when present.
- `sdlc-implement-plan`: dispatches one fresh task agent per safe task, verifies
  scoped worker commits, integrates in stable order, runs combined evidence,
  and non-force-cleans worker resources.
- `sdlc-validate-codes`: runs syntax, lint, type, import, config, dependency,
  build, and locked-slice boundary checks where configured, then uses
  `code-review` in review-only mode to catch blocking implementation-quality
  issues before behavior tests.
- `sdlc-unit-tests`: runs feature behavior, regression, integration, component,
  contract, or mock-based tests, including slice coverage when present.
- `sdlc-evaluate`: observes feature behavior against acceptance criteria and routes
  to GUI, TUI, API, service, or manual evaluation, using a confirmed safe live
  experiment environment only within recorded allowed actions, and records
  planned slice observation when applicable.
- `sdlc-update-documents`: updates project-facing README, changelog, examples,
  usage docs, docs indexes, or generated docs after evaluated implementation or
  UAT evidence, while routing requirements and design drift to their owner
  skills. Multi-layer behavior docs require evaluated slice evidence.
- `sdlc-align-specs`: checks SDLC requirements, design, plans, tests,
  implementation, docs, end-to-end slice evidence, and other evidence for
  consistency before commit or PR readiness.
- `sdlc-classify-failure`: classifies failed phases before retrying and routes to
  the earliest responsible SDLC phase.
- `sdlc-gui-test`: controls and evaluates browser UI flows with screenshots or
  accessibility snapshots when available.
- `sdlc-tui-test`: controls and evaluates terminal, CLI wizard, or TUI flows with
  transcripts and exit-code evidence.
- `sdlc-commit`: seals final integration changes, ff-only promotes the exact
  verified tip, non-force-cleans integration resources, and never pushes or
  replaces the general `commit` or `commit-push` skills.
- `sdlc-uat-tests`: runs product-level user acceptance testing before PR
  creation, using a confirmed safe live experiment environment only within
  recorded allowed operations and reset rules.
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

### `worktree`

`worktree` isolates one selected monorepo project in a sibling
`<repo-name>-worktrees/` directory while retaining a full-repository checkout.
`add` is the default action and always starts a generated `worktree/<name>`
branch at the freshly fetched `origin/main`; dirty or branch-divergent work in
the selected project blocks creation, while unrelated primary-checkout changes
are preserved. `push` and `create-pr` acquire action-bound private publication
reservations, verify managed identity and project-scope containment, then reuse
`commit-push` and `create-pr`. Nested `task-implementer` and Agentic SDLC runs
use owner-bound v2 leases on the outer branch until internal cleanup and final
alignment. `remove` runs from the
primary checkout with an exact generated name and requires durable ownership
state plus a clean worktree and exact merged-PR/head proof, or an unused
never-published branch. It never force-removes a worktree, atomically deletes
the local ref only at its verified SHA, and deletes a surviving remote branch
only when an exact expected-SHA lease still matches.

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
Private prompt-workspace access is opt-in: it can create a `0700`
`$CODEX_HOME/task-implementer` root and add only that exact root to an existing
`workspace-write` configuration without changing sandbox or approval policy.
Its read-only preflight validates this contract with
`--require-task-implementer-workspace`; when persistent access is unavailable,
it reports the per-session `codex --add-dir` remediation.

### `github-workflows`

`github-workflows` is the repository workflow skill for creating, reviewing,
and standardizing GitHub Actions. Use it for PR and merge CI, release
automation, container publication, merge-bot safety, permissions hardening, and
monorepo-friendly workflow structure.

### `global-context-management`

`global-context-management` keeps complex Codex sessions focused and
recoverable by using durable task-state files, limiting noisy parent-thread
exploration, delegating bounded read-only investigation when the current prompt
or a user-enabled local hook policy request authorizes delegation and the
runtime permits it, choosing targeted helper roles after authorization instead
of requiring the prompt to name them or asking for another user prompt, closing
every spawned subagent handle that is completed or no longer needed before
finalizing when close controls are available, reporting any unavailable or
failed cleanup,
and reviewing risk before final answers. Its public skill files stay generic;
local hooks, custom agent config, and task-state files belong under
`$CODEX_HOME`. Normal startup advertises session-scoped task-state paths
without creating missing state files; compaction and the first complex prompt
create only an empty private scaffold. Prompt hooks may suggest bounded same-workspace
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

### `troubleshoot`

`troubleshoot` causally investigates difficult code, shell, CI, installed
software, service, container, network, storage, and infrastructure failures.
It preserves evidence, maintains competing hypotheses, runs discriminating
experiments, proves the earliest causal divergence, applies the smallest
durable repair, and verifies the original failure plus affected boundaries.
Live mutations are bounded to confirmed non-production unless the user
authorizes an exact production action; destructive and high-impact changes
always require action-specific approval.

After one remediation fails against the same blocker, `troubleshoot` starts a
bounded tranche before another repair. The defaults are three distinct failed
remediations or 60 active minutes, whichever comes first. Failures 1 and 2 are
reported as progress; after the exact private state update records exhaustion,
all other tool use stops and a complete user-visible report is returned. A new
explicit user instruction is required for another tranche. The optional
`troubleshoot/assets/hooks` bundle enforces the
recorded private task-state budget at supported `PreToolUse` and `Stop`
boundaries; it does not infer attempts from raw command failures.

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
  `cmp`, `chmod`, `awk`, `cut`, `sort`, `date`, `mktemp`, and `shasum` or
  `sha256sum`
- `python3` when using hook registration

### Usage

```bash
./install-skills.sh [source] [destination_dir]
./install-skills.sh --remove-skill <skill_name> [destination_dir]
./install-skills.sh --install-hooks <source_hook_dir> [--register-hooks] [--refresh-hook-registrations|--replace-hooks-json]
./install-skills.sh --install-all-hooks [--register-hooks] [--refresh-hook-registrations|--replace-hooks-json]
./install-skills.sh --help
```

With no arguments, `./install-skills.sh` uses the directory containing the
script as the source and installs every sibling skill folder that contains
`SKILL.md` into the default Codex target, `~/.agents/skills`.
The `--install-hooks` option is deliberately separate from normal skill
installation. It copies hook files from an explicit source hook directory into
`${CODEX_HOME:-$HOME/.codex}/hooks`, stripping `.template` suffixes for
installed files. It copies missing hook files, leaves matching files unchanged,
records local provenance hashes, and backs up differing existing hook files under
`${CODEX_HOME:-$HOME/.codex}/.install-hooks-state/backups/`, then refreshes
them from the selected source. Add `--register-hooks` to merge that bundle's
`hooks.json` or `hooks.json.template` registration manifest into
`${CODEX_HOME:-$HOME/.codex}/hooks.json`.
The `--install-all-hooks` option is also explicit, but discovers every reviewed
hook-only `*/assets/hooks` directory under this source skills folder and syncs
those payload files in one pass. It does not scan mixed `assets/` directories.
With `--register-hooks`, it also merges each discovered bundle's registration
manifest while preserving existing hook entries. Add
`--refresh-hook-registrations` to replace only differing registrations with the
same event/script and an identical handler list. Add `--replace-hooks-json`
only when you intentionally want to back up and replace `hooks.json` with a
clean file built from the selected source manifests. Hook install modes are
idempotent: unchanged files are not recopied, hook file provenance is recorded,
differing existing hook files are backed up before being refreshed,
registration appends only missing source entries by default, refuses duplicate
Python hook files within the same hook event, and any extra installed hook
files or hook registrations are reported for review instead of removed
automatically.

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

# Copy and register the remediation-budget guard hooks
./install-skills.sh --install-hooks troubleshoot/assets/hooks --register-hooks

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
  `sdlc-start/assets/hooks`, `config-codex/assets/hooks`, or
  `troubleshoot/assets/hooks`. Without
  `--register-hooks`, this only syncs files under
  `${CODEX_HOME:-$HOME/.codex}/hooks`. It records hook file provenance hashes
  and backs up differing existing hook files before refreshing them from source.
- `--install-all-hooks` discovers only skill-owned hook-only directories named
  `*/assets/hooks` under this source folder, checks for conflicting installed
  file names, and syncs all reviewed hook bundles into
  `${CODEX_HOME:-$HOME/.codex}/hooks` with the same provenance, backup, and
  refresh behavior.
- `--register-hooks` can be combined with either hook-install mode. It looks
  for `hooks.json` or `hooks.json.template` in the hook directory or its parent,
  validates the source and destination JSON before syncing hook payload files,
  backs up an existing `${CODEX_HOME:-$HOME/.codex}/hooks.json` before changing
  it, preserves existing entries, and appends only missing source entries. It
  refuses to create or preserve multiple registrations for the same hook event
  and Python hook filename, such as two `Stop` entries pointing at
  `stop_sdlc_continue.py`.
- `agent-nebius-auth-setup` keeps hook installation canonical: setup never
  writes a global project selector, while the root installer syncs hook files
  and `hooks.json` only. It does not migrate inline `config.toml` hook entries;
  it rejects stale legacy agent-nebius-auth entries before copying hooks or
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
