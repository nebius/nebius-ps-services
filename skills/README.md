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
- [Source Hook Catalog](#source-hook-catalog)
- [Skills Installer](#skills-installer)

## Skill Catalog

The catalog below mirrors the live skill folders in this source tree. The
`Invocation` column reflects `agents/openai.yaml`:

- `Implicit allowed`: Codex may select the skill when the prompt matches.
- `Explicit only`: invoke the skill directly with `$skill-name`.

### Alignment and Authoring

| Skill | Invocation | Description |
| --- | --- | --- |
| `align` | Implicit allowed | Project-wide alignment and changed-scope quality gates across code, wiring, tests, CI, CLI/help, config, documentation, workflows, project skills, report-only child code review, lint/syntax, and security. |
| `align-skill` | Implicit allowed | Review, harden, validate, and improve existing or newly scaffolded Codex or Agent Skill folders after an initial scaffold or draft exists. |
| `brainstorm` | Implicit allowed | Explore ideas in chat with relevant source-ranked project, repo, skill, internal, vendor, bounded research for unresolved source conflicts, and advisory design-skill context before implementation. |
| `code-review` | Implicit allowed | Neutral findings-first review of local code; direct `$code-review` runs fix safe scoped findings and validate them with focused repository-native proof, while implicit and nested runs remain report-only. |
| `create-learning-course` | Explicit only | Create public-safe learning courses, course workspaces, syllabi, lessons, exercises, glossaries, and publication review checkpoints. |
| `global-context-management` | Implicit allowed | Keep complex Codex tasks focused with durable task state, concise parent-thread context, targeted read-only subagents when the prompt or local hook policy request authorizes delegation, focused validation, and final risk review. |
| `research` | Implicit allowed | Senior-engineer due diligence on one focal subject or disputed claim, with relevant internal context first, vendor verification, alternatives, and bounded findings for an owning decision. |

### Local Setup and Information

| Skill | Invocation | Description |
| --- | --- | --- |
| `agent-nebius-auth-diagnose` | Implicit allowed | Resolve an explicit or config-owned default-profile Nebius project and diagnose agent-auth or hook failures without mutation. |
| `agent-nebius-auth-setup` | Explicit only | Converge one service account, one exact-permit group, project-bound auth, or a bounded local-repair lease. |
| `sdlc-workflow-test` | Explicit only | Run the unchanged lightweight SDLC verifier or explicitly create, keep, resume, and destroy one owned real three-tier Docker application with computer-use GUI UAT. |
| `attach-ubuntu` | Explicit only | Launch or reuse a disposable Ubuntu Docker container for the current project and best-effort open it through VS Code Dev Containers. |
| `code-info` | Explicit only | Produce read-only project descriptions and code statistics for local folders or GitHub repositories without changing files. |
| `config-codex` | Explicit only | Configure a public-safe local Codex home setup, including global policy, MCP config, hooks, task-state layout, custom read-only agents, owner-correct repo-guard recovery, and validation. |
| `install-grafana-mcp-for-nebius` | Explicit only | Install Nebius Grafana MCP with a pinned human CLI profile, private identity binding, rotating token state, and enforced read-only tools. |
| `nosleep4mac` | Explicit only | Converge one per-user macOS LaunchAgent that keeps the logged-in Mac awake on AC power, including while its screen is locked. |

### Git, Pull Requests, and Publishing

| Skill | Invocation | Description |
| --- | --- | --- |
| `commit` | Explicit only | Create one claim-bound local commit for the complete repository diff across all changed project folders; stages with repo-root `git add -A` inside the exact transaction and never pushes. |
| `commit-push` | Explicit only | Commit all current feature-branch changes from the repo root and push the branch to `origin` without opening a pull request. |
| `create-pr` | Explicit only | Create or reuse GitHub pull requests with branch-safe generic preparation or exact-SHA publication-only behavior for active Agentic SDLC runs. |
| `merge-pr` | Explicit only | Verify and merge a ready GitHub pull request without admin bypass after checking reviews, checks, mergeability, branch state, and head SHA. |
| `publish-helm` | Explicit only | Publish an OCI Helm chart end to end: prepare release changes, PR/merge, tag, wait for workflow, verify the chart, and report the result. |
| `publish-image` | Explicit only | Publish a container image end to end: prepare release changes, PR/merge, tag, wait for workflow, verify image tags/digest, and report the result. |
| `publish-release` | Explicit only | Publish a GitHub Release end to end: prepare release changes, PR/merge, tag, wait for workflow, verify assets, and report the result. |
| `review-pr` | Explicit only | Review a GitHub pull request, fixing safe issues in generic mode or preserving the exact promoted head in active Agentic SDLC findings-only mode. |
| `worktree` | Explicit only | Create full-repository children from the exact clean local feature branch, integrate committed child work through a recoverable validated merge, and remove only with exact local proof. |

### Project Engineering

| Skill | Invocation | Description |
| --- | --- | --- |
| `ai-stack` | Implicit allowed | Select or review an effective, efficient AI stack that satisfies workload requirements across model access, training, inference, agents, interoperability, retrieval, evaluation, safety, and operations with explicit component and evidence classifications. |
| `app-stack` | Implicit allowed | Select the smallest justified application technology stack and emit schema-v2 logical component classes and exact technology decisions for approved scaffold handoffs. |
| `apply-security` | Implicit allowed | Advise on, review, and safely remediate security issues across design, implementation, infrastructure, deployment, Helm, Kubernetes, Terraform, CI/CD, shell, and application code. |
| `container` | Implicit allowed | Build, review, harden, troubleshoot, and validate OCI images, Docker/BuildKit workflows, Compose stacks, runtime contracts, multi-platform and GPU containers, and supply-chain evidence. |
| `design` | Implicit allowed | Design software features, APIs, vertical slices, and proven remediation handoffs before implementation, using `research`, `app-stack`, `ai-stack`, and `system-design-rules` before `/plan`. |
| `frontend-project` | Implicit allowed | Materialize exact React, TypeScript, and Vite frontend files from fixed decisions, including deterministic candidate manifests and public environment schemas. |
| `github-workflows` | Implicit allowed | Create, review, or standardize GitHub Actions for PR/merge CI, merge automation, reusable workflows, permissions, and release/image YAML. |
| `gitignore` | Implicit allowed | Create or update stack-aware `.gitignore` files with sensible macOS, VS Code, and detected language/tool defaults. |
| `helmchart` | Implicit allowed | Create, review, harden, refactor, lint, template, or standardize Helm charts and chart CI. |
| `linter` | Implicit allowed | Lint and conservatively auto-fix shell, Markdown, and Python files with tools such as `shellcheck`, `markdownlint`, and Ruff. |
| `maintain-project-specs` | Implicit allowed | Create, migrate, validate, and reconcile canonical project requirements/design, capture semantic compatibility intent, coordinate deferred project `AGENTS.md` sealing, and enforce the prompt-to-implementation lifecycle through hook guardrails. |
| `nebius` | Implicit allowed | Automate Nebius SDK/cloud workflows for IAM, object storage, VPC, quota, MK8s readiness, GPU/operator decisions, and observability wiring. |
| `nebius-audit-log` | Explicit only | Query Nebius Control Plane Audit Logs by resource or current subject with bounded, sanitized read-only CLI output. |
| `nebius-grafana-query` | Implicit allowed | Query authorized metrics, logs, dashboards, and traces through human-authenticated Nebius Grafana, returning either ranked reports or bounded structured evidence facts. |
| `optimize-pytest` | Implicit allowed | Measure, review, and safely optimize pytest suite performance with phased evidence, cumulative-cost analysis, and like-for-like validation. |
| `project-agent-instructions` | Explicit only | Conditionally render and terminally seal concise selected-project rules from shared-owner specs, including explicit existing-user compatibility intent, with nearest-marker discovery, managed-tail ownership, guarded retirement, and fail-closed recovery. |
| `prompt-session-intake` | Explicit only (hook-routed) | Capture eligible direct prompts from a bound Task Implementer or Agentic SDLC objective as a non-blocking sidecar, preserve private provenance, and coordinate lossless exact-once prompt updates without invoking either workflow. |
| `python-project` | Implicit allowed | Scaffold or harden Python projects with modern packaging, `src/` layout, Ruff, pytest, Typer, Pydantic, services, APIs, and CI. |
| `scaffold-project` | Explicit only | Own repository topology, exact technology-to-unit binding, per-path routing, candidate approval, digest locking, validation, and guarded scaffold apply after architecture approval. |
| `shell-scripting` | Implicit allowed | Create, refactor, or review Bash automation with strict mode, safe argument parsing, idempotency, and readable CLI output. |
| `system-design-rules` | Implicit allowed | Evaluate system designs, ADRs, architecture options, APIs, data ownership, reliability, security, observability, scale, cost, and team boundaries with a practical design checklist. |
| `task-implementer` | Explicit only | Create persistent per-project lanes, run durable dependency waves with fail-closed current-schema recovery, integrate pending generations, and explicitly remove idle lanes. |
| `task-implementer-test` | Explicit only | Run lightweight Task Implementer verification or own one replaceable disposable multi-tier live fixture. |
| `terraform` | Implicit allowed | Scaffold, standardize, or improve Terraform repositories and modules with state guidance, validation, security controls, examples, and CI. |
| `troubleshoot` | Implicit allowed | Discover deployed stacks, verify components and layered logs, causally debug difficult code and infrastructure failures, and gate completion on a canonical evidence matrix. |

### Agentic SDLC Workflow

All `sdlc-*` skills are explicit-only. The phase skills run through the
Agentic SDLC workflow, starting with
`$sdlc-start workspace init [project-folder]` and then
`$sdlc-start run <prompt-ref-or-file>`; the external
`sdlc-workflow-test` verifier is not a phase. `project-agent-instructions` is
shared explicit-only runtime support and a golden-path step after design;
`troubleshoot` is required runtime support for ambiguous failure diagnosis but
remains absent from the golden phase sequence.

| Skill | Invocation | Description |
| --- | --- | --- |
| `sdlc-align-specs` | Explicit only | Check SDLC requirements, design, plans, tests, implementation, documentation, end-to-end slice evidence, and other evidence for consistency. |
| `sdlc-auto-steering` | Explicit only | Refresh private active-run steering by recording mid-run user prompts, classifying them, and deriving compact reminders before the next SDLC phase. |
| `sdlc-classify-failure` | Explicit only | Validate normalized failure/diagnosis records, enforce repair budgets and design admission, and route proven causes or conditional troubleshooting. |
| `sdlc-commit` | Explicit only | Seal final integration changes, ff-only promote the exact verified tip to the unchanged project branch, and non-force-clean integration resources; never pushes. |
| `sdlc-create-design` | Explicit only | Create initial design or perform evidence-gated failure redesign with positive system-contract proof, stable feature IDs, and required approval. |
| `sdlc-create-plan` | Explicit only | Create a locked task graph or append-only corrective plan vN+1 that preserves completed definitions/digests and binds the diagnosis/oracle. |
| `sdlc-create-requirements` | Explicit only | Create or update `docs/requirements.md` from user prompts, tickets, stories, change requests, and optional safe live experiment environment details while preserving stable requirement IDs. |
| `sdlc-evaluate` | Explicit only | Evaluate acceptance criteria and emit normalized, commit-bound failure events; use Grafana only for a predefined evidenced operational gate. |
| `sdlc-gather-context` | Explicit only | Build compact feature context packs from product, vendor, internal, codebase, layer-boundary, and test sources. |
| `sdlc-gui-test` | Explicit only | Control and evaluate GUI behavior through Computer Use, Browser, or Playwright as required, with screenshots or accessibility snapshots. |
| `sdlc-implement-plan` | Explicit only | Coordinate immutable dependency/corrective waves, preserve diagnosis/oracle bindings, integrate in order, rerun invalidated evidence, and clean without force. |
| `sdlc-merge-pr` | Explicit only | Merge a specific Agentic SDLC pull request only after explicit user request, final readiness checks, and exact promoted/reviewed head verification. |
| `sdlc-prepare-execution` | Explicit only | Prepare or resume the persistent feature integration worktree and deterministic task waves after plan lock and before TDD. |
| `sdlc-start` | Explicit only | Coordinate prompt-bound state, authoritative repair-control pointers, conditional diagnosis, and exactly one next phase. |
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

Append `--help` or `-h` to any repo-owned skill for concise, report-only help:

```text
$task-implementer --help
```

Help reports the skill's purpose and invocation policy, shows exact usage for
every public action, and describes every public action, positional argument,
and flag in one concise line. It includes `-h, --help`, says when no additional
public flags exist, never exposes private helper actions or flags, and
identifies internal or coordinator-only skills as having no standalone public
workflow action. It stops after Codex loads the selected `SKILL.md`, before
project inspection, additional tools, workflow execution, or mutation. A help
request is not authorization to run the skill's workflow.

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
invocation policy, standard `## Help` and `## Learning Loop` sections, and
optional `references/`, `scripts/`, `assets/`, and `evals/` only when they
serve the skill.

### Prompt Examples

```text
$commit Commit the complete repository diff across every changed project folder on this branch without pushing.

$commit-push Commit all current changes on this feature branch, generate a commit message, push it to origin, and tell me whether the worktree is clean.

$create-pr Create a PR for the current local work, using a new prep branch if I am still on the default branch.

$create-pr Resolve conflicts for the current branch against main, open or reuse its PR, and return the PR URL.

$worktree Create an isolated worktree from my current clean feature branch for the current monorepo project.

$worktree integrate project-fix-trigger-validation-a7c2f9 from the primary checkout; safely commit eligible ordinary child and source dirt before validating the combined result.

$worktree remove project-fix-trigger-validation-a7c2f9 after verifying its exact local integration proof.

$review-pr Review PR #110 against the base branch, fix safe issues on the branch, and tell me whether it is ready to merge.

$review-pr Review https://github.com/example-org/example-repo/pull/42, resolve straightforward conflicts against main if the branch is writable, and report remaining blockers.

$merge-pr Merge PR #110 with squash after verifying checks, reviews, mergeability, and the head SHA without using admin bypass.

$publish-image --mode complete --tag 1.2.3 --image-name ghcr.io/example-org/example-app prep, PR, merge, tag, wait for CI, verify the image digest, and report the published artifact.

$sdlc-workflow-test Verify the Agentic SDLC workflow against docs/agentic-sdlc-design.md and write a safe report.

$sdlc-workflow-test --create --keep

$sdlc-workflow-test --resume

$sdlc-workflow-test --destroy

$sdlc-start workspace init services/example-app

$sdlc-start run <prompt-ref-or-file>

$align-skill Review and standardize skills/foo against the canonical skill structure and official vendor docs.

$align-skill Harden this scaffolded skill folder into a safe, secure, fast Codex skill, then validate it.

$brainstorm Explore this architecture idea, gather the relevant project docs, related skills, internal context, and official vendor docs, and challenge weak assumptions before we implement anything.

$troubleshoot --attempt-limit=10 --time-limit-minutes=180 Diagnose this persistent failure and repair it only after proving the cause.

$create-learning-course Create a public-safe course workspace for engineers learning Kubernetes networking, with mission, syllabus, sources, HTML lessons, exercises, glossary, and publication review.

$research Research Kubernetes Gateway API, search internal Slack and Confluence context first if relevant, explain how it works internally, identify limitations and alternatives, and recommend when we should or should not use it.

$design Design this new feature before implementation: read the requirements, inspect the existing code and docs, route unfamiliar topic and technology research through $research, use $app-stack for undecided application layers, use $ai-stack for undecided AI layers, apply $system-design-rules to the non-trivial design decisions, compare options, and create a /plan handoff.

$app-stack Select the smallest justified stack for this application, mark optional components with their adoption triggers, and coordinate implementation through matching specialist skills.

$ai-stack Select an effective, efficient AI stack that satisfies these workload requirements, classify every component and evidence claim, and define validation and switch conditions.

$scaffold-project Plan a multi-component repository from this approved architecture, show every owner and brownfield merge, and do not apply it until I approve the exact digest.

$frontend-project Create a React, TypeScript, and Vite component with strict type checking and tests, but do not install dependencies.

$container Review this repository's image and runtime contract, harden the production path, validate what can be proven locally, and report untested platforms and sibling-skill handoffs.

$code-review Review the current local branch, fix only safe in-scope findings, validate each fix with focused repository-native checks, and report the prioritized fixed and gated findings.

$system-design-rules Review this ADR against the system design checklist, compare the trade-offs, and identify missing reliability, data, security, observability, cost, and ownership decisions.

$task-implementer workspace init services/nebius-cxcli

$task-implementer workspace reuse services/nebius-cxcli

$task-implementer run <prompt-ref-or-file>

$task-implementer integrate services/nebius-cxcli

$task-implementer workspace remove services/nebius-cxcli

$task-implementer-test

$task-implementer-test --create --keep

$task-implementer-test --destroy

$apply-security Scan this repository for infrastructure, CI/CD, shell, and application security issues, then produce a prioritized remediation plan with safe patch candidates.

$code-info Gather read-only project info from this folder or a GitHub repo with a concise description, documented features, three-level CLI hierarchy, LOC, packages, dependencies, size comparisons, repo size, tests, artifacts, and coverage.
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
cross-code validation, report-only `code-review`, `linter`,
`apply-security`, and focused repository-native tests or builds. `align` owns
safe remediation from its child review, reports risky blockers for explicit
approval, and resolves `apply-security/SKILL.md` directly when the mandatory
security lane is not visible in the initial skills list. Its final gate prefers
no-write/no-cache settings and removes exact task-created validation artifacts.

### `align-skill`

`align-skill` reviews and hardens one or more existing or newly scaffolded Codex
or Agent Skill folders. Use it for named skills, local skill folders,
multi-skill parent folders, GitHub skill repositories, or GitHub tree URLs when
`SKILL.md`, trigger metadata, references, assets, scripts, safety guardrails,
official vendor-doc verification, canonical structure, validation evidence, fast
authoring practices, optional stateful-workflow section profiles, and reusable
learning capture in local skill source materials need to be aligned. It adds or
repairs concise report-only Help for every created or aligned skill, covering
each public action, positional argument, and flag. Before it claims a target
skill is aligned, it applies `code-review` in review-only mode and
`apply-security` in advisory or scan mode to the target skill scope, and it
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

`research` performs senior-engineer technical due diligence on one focal
technology, framework, API, protocol, RFC, product, architecture pattern,
feature requirement, or disputed claim. For organization-tied questions, it
searches internal Slack and Confluence context through available connectors
first, uses MCP or app-tool
access for internal systems when connectors are unavailable, then verifies
technical claims against official vendor documentation and authoritative
external sources. The output cross-checks internal and external evidence and
labels organization-specific guidance, vendor-documented behavior, general
industry practice, and unverified claims. It may compare alternatives as
evidence but does not choose technology for an application or AI layer; those
decisions belong to `app-stack` or `ai-stack`. Use `brainstorm` for open-ended
ideation, `design` for solution design and `/plan` handoff, and
`system-design-rules` for checklist review of an existing proposal.

### `design`

`design` turns requirements, existing-system evidence, and `research`-backed
topic, requirement, and technology due diligence into a concrete software
design before implementation. It follows a phased workflow: understand
requirements, understand the existing system or greenfield context, route
missing knowledge through `research` when available, route undecided
application-stack or layer technology choices through `app-stack`, route
undecided AI-specific choices through `ai-stack`, design the solution, apply
`system-design-rules` to non-trivial solution decisions,
evaluate alternatives, define vertical end-to-end slices for serial multi-layer
applications, and create a Codex `/plan` handoff. Use it for new features,
major changes, APIs, data flows, integrations, and new applications when the
user wants a practical design and implementation-ready plan, not immediate
coding. Use `brainstorm` for open-ended ideation, `system-design-rules` for
checklist review of an existing proposal, `app-stack` directly for a
product-stack-only request, `ai-stack` directly for an AI-stack-only request,
and `sdlc-create-design` for Agentic SDLC-owned `docs/design.md`. It also
accepts a proven causal handoff from `troubleshoot`
when the durable remediation changes a system contract such as a component
boundary, public interface, data owner, migration, or cross-component workflow.
Unknown causes and complex repairs inside one existing private boundary remain
in `troubleshoot`.

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
solution design, `ai-stack` for undecided AI-specific layers, and stack-specific
skills directly when the stack is already fixed and no selection decision
remains. When `design` delegates a scoped stack decision, `app-stack` returns it
to the active design workflow instead of starting a recursive handoff.

For a complete scaffold, its schema-v2 handoff is logical only: closed
component class, component status, canonical technology name,
technology/profile/version decisions, selected capabilities, constraints,
validation expectations, and revisit triggers. It never assigns repository
paths, materialization units, runtime units, candidate sets, file owners, or
apply authority.

### `ai-stack`

`ai-stack` selects, reviews, simplifies, or modernizes the AI-specific layers of
a system: model access, training and tuning, inference and serving, agents and
durable execution, MCP/A2A and agent-facing UI, retrieval and RAG, evaluation,
safety, and operations. It freezes materially different workload contracts,
starts from the current or no-new-component baseline, applies hard gates before
scoring candidates, and assigns every component `Required`, `Conditional`,
`Deferred`, or `Rejected` plus every material claim `Measured`, `Officially
documented`, or `Assumed`.

The skill keeps its dated Python-first baseline replaceable: Pydantic AI for a
bounded typed agent, LangGraph only for explicit graph semantics, Temporal for
durable cross-service execution, an internal capability-aware provider contract
only when semantic portability is real, official Tier 1 MCP SDKs as conformance
authority, and benchmark-gated PyTorch, serving, retrieval, MLflow, and
OpenTelemetry layers. Use `app-stack` for the surrounding product stack,
`research` for deep due diligence on one choice, and `design` for cross-layer
synthesis and `/plan`. Its optional implementation handoff is logical-only and
does not assign repository paths, runtime units, candidate manifests, or file
owners.

### `scaffold-project`

`scaffold-project` is the explicit-only composition layer between an approved
design or stack and specialist-owned project artifacts. It separates logical
capabilities, physical materialization units, and runtime units; materializes
only required items; assigns one owner to every normalized path; and gathers
exact candidates that satisfy positive owner artifact contracts from Python,
frontend, container, Terraform, Helm, GitHub Actions, `.gitignore`, and shell
specialists.

Planning and candidate generation use a private bundle. A closed canonical JSON
manifest binds architecture inputs, target identity, before/after hashes, file
type and mode, payloads, candidate-set identity, normalized inputs and their
digests, manifest digests, validation binding, and deterministic operation
order. Each operation is bound to one candidate set and materialization unit;
every app-stack-backed capability must retain its approved kind and exact
technology; all of its assigned units retain the canonical technology and
language, and runtime units bind back to the capability so shared source roots
cannot mix runtime contracts. External services cannot substitute another
technology under the same ID. Frontend
candidates must additionally remain below a frontend-owned React/Vite root and
match the app-stack-approved package manager, versions, runtime, and declared
frontend capability selections. Unsupported required frontend profiles fail
closed. Brownfield merges are exact additive suffixes on approved integration
files only. The guarded executor is the only target writer. Known conflicts
block all writes,
interrupted applies retain a private journal with created-directory identities,
and reruns classify each path as before, after, or conflict without automatic
rollback. Schema-v1 bundles are rejected rather than translated. It never
deletes files, runs native generators, installs dependencies, initializes Git,
provisions, deploys, publishes, commits, pushes, opens a PR, or starts Agentic
SDLC.

### `frontend-project`

`frontend-project` owns React, TypeScript, and Vite package metadata,
configuration, entrypoint and route shells, tests, public environment schema,
explicitly assigned component-local lint/format tooling, and component
documentation. Its React/TypeScript/Vite producer validates a closed assigned
path set and emits exact deterministic candidates with input, file, and
validation provenance. `.env.example` contains names only and `src/env.ts`
enforces the allowlisted public `VITE_*` contract. Standalone scope owns the
selected frontend root; coordinated-candidate scope writes only to the private
bundle for `scaffold-project`. Root CI, ignore rules, Docker, infrastructure,
deployment, and agent instructions remain with their specialist owners.
The deterministic renderer accepts only npm, pnpm, Yarn, or Bun and rejects
secret-like public names, including compact API-key and access-key markers.

### `container`

`container` owns container engineering from repository source through a
validated OCI image and documented runtime contract. It covers Dockerfile and
Containerfile design, build contexts, BuildKit/buildx, local Docker execution,
Compose development/test and approved single-host production profiles,
non-root and read-only hardening, signals, health, storage, networking,
multi-platform evidence, GPU requirements, and supply-chain policy. The
retained typed Python and React/Vite renderer blocks instruction injection and
keeps its local Compose schema fail-closed; broader Compose review uses a
separate audit path. Builds, pulls, runs, and networked scans remain explicit
local opt-ins. `github-workflows` owns CI YAML, `publish-image` owns registry
publication and signing actions, and `helmchart` owns Kubernetes resources
derived from the container runtime handoff.

### `code-review`

`code-review` performs a neutral, evidence-based review of the current local
branch, local diff, changed files, module, repository area, or provided patch.
Use it when the user wants findings-first feedback on bugs, regressions, test
gaps, reliability risks, security-adjacent issues, maintainability,
abstraction quality, modularity, type boundaries, file-size growth, spaghetti
branches, and missed structural simplifications. A direct standalone
`$code-review` invocation completes the review first, fixes only safe in-scope
findings, validates each fix with declared red-before/green-after focused proof
plus the narrowest affected repository-native checks, reviews only its touched
diff, and returns the prioritized fixed and gated ledger. An already-green or
unrelated check cannot authorize remediation. The skill itself never resolves,
loads, or invokes `align`, and it does not suppress a separate
outer-orchestrator policy requiring alignment after changes. Implicit
selection, nested use, and explicit no-write requests such as review-only,
audit-only, or report-only remain non-mutating and must not leave validation
artifacts behind. Priority is independent of auto-fix safety: P0 is Critical
and highest, followed by P1 High, P2 Medium, P3 Low, and Nit. `review-pr`
remains the GitHub PR review, readiness, and branch-update workflow, while
`align` remains the separate project consistency and repair workflow with a
report-only child review lane. Use
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
creates or reuses a persistent full-repository lane from the exact committed
`HEAD` of a named non-default source branch, creates or verifies the private
workspace, creates one starter prompt only when none exists, asks VS Code to
reuse its last active window, and is safe to repeat without changing prompts or
history. Dirty source-checkout state is allowed and excluded from the lane
baseline. Loading the workspace restarts that
window's extension host and may interrupt its terminal or Codex UI; editor
failure remains non-fatal. One `run <prompt-ref-or-file>` validates
and snapshots exact prompt bytes, creates or reconciles the internal task queue,
locks dependencies, exact/prefix write claims, conflict domains, validation,
and done criteria, then reserves an exact whole-repository lane tree and path
digest for review before atomically checkpointing that token as one clean
direct-child baseline and coordinating
every wave until completion or a blocker. A clean lane creates no checkpoint
commit, active-generation dirt still blocks, and the primary checkout is never
mutated. The post-checkpoint clean `HEAD` becomes the generation, coordinator,
and first-wave baseline.
Each completed run releases one immutable lane generation; back-to-back runs
may accumulate pending generations while leaving the source checkout untouched.

`workspace reuse [project-folder]` reopens the exact existing generated VS
Code workspace from either the primary source project or its owning managed
lane. It rejects unrelated worktrees and unavailable source refs, validates
workspace and live lane identity, but never refreshes the lane, migrates
prompts, checkpoints dirt, queues work, or claims readiness. Missing or
mismatched state fails closed; `run <prompt-ref-or-file>` remains
valid directly from the primary source project without reopening first.

An explicit init or run binds the current Codex session. In that bound session,
every direct prompt still runs normally in the current agent. The separate
`prompt-session-intake` hook may also stage safe input as a non-blocking
sidecar; the agent refines material intent losslessly and the Task Implementer
prompt adapter applies a stale-base-checked create or append exactly once per
operation ID without starting or resuming the workflow. Only explicit runs
register the active prompt for unique
fresh-session attachment and close it only after verified terminal completion.
Conversation, status, control, secrets, and failed capture remain nonmaterial
and never block direct delivery; captured updates and file edits never auto-run.

Users steer the workflow by editing the same prompt—preferably appending to its
optional `## Steering` section—and running the same command. There is no public
hash-maintenance step: exact-byte and normalized-intent digests detect the edit,
while a shared owner requires complete statement-to-contract impact evidence
before Task Implementer may retain or rebuild its plan. A bare `no_effect`
decision cannot unlock work, and public status exposes no prompt-derived
digest. There is no public `steer` command or user-supplied ID. Steering may
safely recompute a merely
planned wave. Once worktrees or assignments exist, it queues without changing
the immutable active wave and is reconciled before promotion or at the next
wave boundary.

Requirements are normalized before tasks into stable `TI-REQ-nnn` records, and
each selected task receives a `TI-DES-nnn` design. The coordinator creates or
updates only marked regions in project
`docs/requirements.md` and `docs/design.md`, byte-preserving generic content
outside them. Agentic SDLC ownership or malformed/unsafe managed state fails
closed. Workers never edit these shared specifications concurrently.

After both managed specifications validate, the coordinator explicitly routes
to `project-agent-instructions` with a private receipt binding both complete
files and their traceability. It deterministically renders the exact
selected-project `AGENTS.md` only when tracked evidence establishes durable
project rules. Personal global instructions never change repository bytes.
Clear intent that existing users depend on safe code or interface changes is
treated as compatibility intent without requiring a magic keyword; it protects
supported public APIs/imports, CLI behavior, configuration, persisted formats,
and upgrade paths unless active project instructions already do so. Nested
selected projects resolve the nearest effective marker ancestor for discovery
without moving their target scope.
Human-owned or edited files are preserved; adoption and retirement require
exact-digest approval, and recovery artifacts block every transition. Its
private manifest, decision, ownership, and verified state are resolved before
the contract commit and worker dispatch. A project-file change requires a fresh
Codex session; `workspace init` remains private-state-only.

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

The coordinator verifies its task commits and changed paths, merges task branches
into a temporary integration branch in stable task-ID order, runs combined
validation and review, reconciles steering, then removes clean worker
worktrees and deletes their refs at exact expected SHAs. It advances the
unchanged persistent lane branch under the shared lock with
`git merge --ff-only`; tasks become done after promotion. Integration cleanup
then removes its worktree before exact-SHA ref deletion. Any failure retains
exact recovery resources and leaves unverified work intact.

At wave planning, the coordinator acquires the next exact lane generation and
registers repository-wide exact/prefix and conflict-domain claims. Overlap with
another project lane blocks before worker resources exist. Replanning extends
those claims before replacement state is written and retains prior claims until
generation integration. Globally exclusive live-action classes also register
class-wide domain claims so separate lanes cannot bypass singleton behavior
with different keys. It never fetches, resolves a remote default, or mutates
the source checkout.

`integrate [project-folder]` requires the recorded source checkout and lane both
clean with no active generation. It serializes the source ref, validates one
exact two-parent candidate for every pending generation, promotes by expected-
old source-head proof, then fast-forwards and rearms the same lane while
releasing its claims. `workspace remove [project-folder]` explicitly removes
only an idle, clean, fully integrated lane; prompts and run history remain, and
later initialization creates a new lane incarnation. Public `$worktree`
lifecycle actions reject Task Implementer lanes.

The helper uses only the Python standard library, applies private POSIX modes,
rejects path and symlink escapes, journals Git mutations, and never prints
prompt bodies. Every v1 execution record fails with
`WORKFLOW_UPGRADE_REQUIRED`; no legacy execution schema is readable. The Skill
is explicit-only. Use `global-context-management` for general context hygiene,
`$sdlc-start run <prompt-ref-or-file>` for Agentic SDLC, and
`align` for final alignment.

### `task-implementer-test`

`task-implementer-test` verifies Task Implementer without using a real user
project. With no flags it runs the current explicit-only and exact five-action
contract checks plus the local temporary-fixture workspace, specification,
scheduler, Git-wave, persistent-lane, verifier-helper, lifecycle, reporting, and
semantic suites. It never starts Docker, dispatches implementation workers, or
creates a persistent application.

The opt-in `--create` mode first replaces any prior exactly owned verifier
generation, then exercises the real Task Implementer public interface on a
seeded local-only brownfield task board with an owned bare origin. The target is a browser frontend,
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
hook fixtures. It also checks normalized repair-control, conditional
troubleshooting, positive design admission, and corrective-plan/wave
contracts. Optional private live-results evidence covers the golden path,
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
discovers the project from current-session evidence and treats persistent
memory only as a corroborated hint. When no explicit task project exists, the
runtime hook clears ambient Nebius auth state, resolves the config-owned
default with `nebius profile current`, and reads only that profile's configured
`parent-id`. It never infers authority from ambient profiles, credential
filenames, cwd, legacy default selectors, or unrelated task state.

Project selection is task execution context, not a typed skill argument.
Implicit skill selection and hook feedback do not persist the project ID. Once
an explicit task project is selected, the agent carries it for the current task
into every Nebius-sensitive Bash payload. Without one, the hook repeats its
sanitized default-profile lookup. A later explicit project replaces that
fallback, while unresolved conflicting task evidence must ask rather than
guess.

Correctable command-policy denials are fixed and retried by the agent without
running setup: add exactly one explicit task selector as the first raw token of
the entire outer Bash payload, repair a missing default-profile `parent-id`,
remove a conflicting explicit profile or managed-auth assignment, or replace a
token-printing command with the normal operation or redirected verification
form. Raw-token helpers always require the explicit outer selector.
Mixed local/Nebius payloads are split into separate Bash calls so local-only
commands remain unprefixed and receive no injected credential context. When
the project is already authoritative, the agent retries the corrected payload
once without rediscovery or `verify`.

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
`$sdlc-start run <prompt-ref-or-file>`, then let the coordinator
record the next recommended phase in local run state. Editing the same managed
prompt and repeating `run` is the steering path; bare `$sdlc-start` is not a
resume interface.
After an explicit init or run binds the session, `prompt-session-intake` may
stage later safe direct-turn metadata as a non-blocking sidecar while the
current agent handles the request normally. It never stores the submitted body.
Agent-owned semantic selection keeps only durable project intent, including
commands used as project contracts but excluding workflow/skill, shell/tool,
delivery, agent-control, status, conversation, and unrelated wrappers. The
SDLC adapter rehashes the accepted projection and binds its digest to the
operation marker before compare-and-set create or append. Exact retries and
byte-identical projections do not append again, capture never starts or resumes
SDLC, and distinct concurrent drift never auto-rebases. Only explicit runs register and
terminally close the active prompt so
a fresh session attaches only when the objective is unique. Conversation,
status, control, and prompt-file saves never auto-run.
The committed project truth is `docs/requirements.md`, `docs/design.md`, and,
only when the evidence gate requires it, a provenance-owned project-root
`AGENTS.md`. For each accepted prompt revision, the shared owner validates one
complete private statement-impact claim before execution planning. A later
proven no-effect revision may retain an older feature plan; contract or
execution effects and spec drift require safe replanning. Editable prompts
receive no self-hash and still execute only through explicit `run`. The shared
`project-agent-instructions` skill makes the instruction decision after design
and before auto-steering or planning.
Explicit existing-user no-break intent is rendered for the current execution
and persisted at terminal seal; a conflicting personal global default cannot
suppress the project rule.
Private run state, plans, evidence, screenshots, transcripts, and steering live
under `~/.codex/sdlc-runs/<project-id>/<run-id>/` and must not be committed.
Each active feature also has schema-v7 execution state and private worktrees
there. After plan lock, `sdlc-prepare-execution` creates a persistent
integration branch/worktree and enforces the initialized monorepo folder as the
claim and worker-cwd boundary. Task Implementer and Agentic SDLC remain
separate peer workflows: both use Worktree infrastructure, but Agentic SDLC
rejects an active Task Implementer persistent lane rather than nesting or
sharing execution state. `sdlc-implement-plan` runs safe tasks in enforced
capacity batches inside dependency waves, using one fresh native agent or
sequential ephemeral `codex exec` fallback per task and immutable direct-
predecessor handoffs. It arms only available slots, requires direct bounded
worker heartbeats, watches liveness every 30 seconds, and gives scope violations
precedence over allowed in-claim prestart mutation. Its sequential fallback
terminates the worker's whole process group on any post-spawn failure. An
untouched worker that never started can be requeued only after confirmed stop
and an exact dispatch compare-and-swap. The coordinator journals an exact
tree/message/evidence finish intent before creating each task commit and retry
adopts only the matching clean direct-child commit and exact result. It retains
coordinator-created task commits and ordered merge commits and cleans only
proven reachable resources without force. The
project promotion branch stays unchanged until `sdlc-commit` seals the final
integration tip and promotes it under the shared Git lock with
`git merge --ff-only`. Unmanaged runs reverify the recorded remote-default
branch and HEAD; managed children instead retain their exact local identity and
later return the recorded primary path plus the exact `$worktree integrate`
handoff, then stop for a fresh explicit user invocation from that primary
checkout without child publication.
Project-level managed prompts and immutable run revisions also remain under
`~/.codex/sdlc-runs/<project-id>/`. `STEERING.md` is the active-run inbox and
steering ledger for accepted prompt revisions, while
`steering/auto-steering.json` stores machine-readable
dispositions and compact reminders. Requirements, design, or generated
project-instruction changes captured in steering still route through their
owning skills before implementation treats them as true.
`docs/requirements.md` may also record an optional Live Experiment Environment
so later evaluation and UAT can use a confirmed non-production or disposable
target with safe connection, allowed-action, reset, and evidence rules.
Optional global PreToolUse and Stop hooks can enforce SDLC invariants from that
local state. The Stop hook repeats the prompt-bound `sdlc-start run` command
rather than routing directly into phase skills. The PreToolUse registration is
matched by tool name, but it immediately allows calls outside an active SDLC
run and omits a static SDLC status message so ordinary tasks are not presented
as SDLC work.
Sensitive Git actions use
short-lived local authorization files under the active run's `permissions/`
directory; the skills create those files only immediately before the guarded
action. Registered integration and worker worktrees remain inside hook policy
even outside the original checkout, with exact Git identity and action-scoped
authorization checks for sensitive raw Git operations.
PR publication authorization binds the `create-pr` phase, branch, actual
remote-default base, clean exact promoted HEAD, passing UAT status, and expiry.
It guards active-run pushes and PR creation without blocking read-only PR
inspection.
The canonical source for those optional SDLC hooks is
`sdlc-start/assets/hooks/`. Patch that source first, validate it with
`sdlc-start/assets/hooks/tests/test_sdlc_hooks.py`, and sync reviewed hook
bundles deliberately with `./install-skills.sh --install-all-hooks
--register-hooks --refresh-hook-registrations`; installed copies under
`$CODEX_HOME/hooks` are runtime artifacts. The refresh option replaces only a
differing same-event, same-script registration with the same handlers,
allowing only `statusMessage` metadata to differ, and preserves unrelated
entries.
Keep these SDLC hooks separate from the non-SDLC global-context hooks:
`SessionStart` is for stable global context and task-state location, and
the global-context `UserPromptSubmit` remains limited to lightweight prompt-time
context, safety, or opt-in delegation requests. The separate
`prompt-session-intake` hook owns private capture for explicitly bound prompt
workflows and does not refine, edit, or execute work from the hook process.
See `docs/agentic-sdlc-design.md` for the architecture, template ownership,
local state layout, hook boundaries, and full skill-by-skill lifecycle.

- `sdlc-create-requirements`: creates or updates `docs/requirements.md` from user
  prompts, tickets, approved change requests, and optional safe live experiment
  environment details while preserving stable `REQ-*` IDs.
- `sdlc-start`: initializes the private prompt workspace, accepts immutable
  prompt-v3 revisions with Ask-only required input, compiles requirements with
  selective stable clarifications, durably queues explicit cross-prompt run
  requests, coordinates the active SDLC run, reads steering and local
  checkpoints plus authoritative repair pointers, and chooses one next skill
  without duplicating history. It keeps troubleshooting conditional and routes
  every diagnosis back through classification. At run start, it encourages
  safe live experiment environment capture through requirements.
- `sdlc-gather-context`: builds compact feature context packs from official docs,
  internal sources, code, tests, and layer-boundary evidence when a vertical
  slice may apply.
- `sdlc-create-design`: creates or updates `docs/design.md`, maps requirements to
  stable `FEAT-*` blocks, records selected and rejected design options, and
  defines vertical feature flow, layer map, implementation, validation, test,
  evaluation, rollout, and rollback boundaries. Failure-driven redesign
  additionally requires positive system-contract proof, valid
  evaluator/environment, reproducibility, high confidence, affected-feature
  closure, rollback, and durable approval for broader changes.
- `project-agent-instructions`: after an owner receipt validates requirements,
  design, and traceability, decides whether tracked evidence justifies durable
  selected-project rules; deterministically renders within a 2 KiB preferred
  and 4 KiB hard budget, preserves human-owned files, binds v3 managed output
  to private ownership, guards adoption/retirement, resolves the nearest
  effective marker ancestor for nested discovery, fingerprints effective Codex
  config, and rejects untracked evidence or unresolved recovery state.
- `sdlc-auto-steering`: refreshes private active-run steering, records every
  mid-run user prompt safely, classifies entries, derives compact reminders,
  and routes requirements, design, project-instruction, docs, or human-input
  changes back through `sdlc-start`.
- `sdlc-create-plan`: creates locked private local execution plans for one feature,
  preserving the end-to-end slice and defining stable dependency-safe task
  records. Post-wave correction creates immutable adjacent plan vN+1,
  preserves full prior task definitions/digests, and appends
  diagnosis/oracle-bound tasks and waves.
- `sdlc-prepare-execution`: validates the task graph and prepares the persistent
  feature integration worktree and deterministic waves before TDD.
- `sdlc-tdd`: writes or maps tests in the integration worktree before
  implementation, including planned slice contracts and cross-layer validation
  targets when present.
- `sdlc-implement-plan`: dispatches one fresh task agent per safe task, verifies
  direct heartbeats and scoped coordinator-created task commits, integrates in
  stable order, runs combined evidence,
  and non-force-cleans worker resources. Corrective work runs the original
  oracle first, then the affected boundary and every invalidated downstream
  gate at the new integration commit.
- `sdlc-validate-codes`: runs syntax, lint, type, import, config, dependency,
  build, and locked-slice boundary checks where configured, then uses
  `code-review` in review-only mode to catch blocking implementation-quality
  issues before behavior tests.
- `sdlc-unit-tests`: runs feature behavior, regression, integration, component,
  contract, or mock-based tests, including slice coverage when present.
- `sdlc-evaluate`: observes feature behavior against acceptance criteria and
  routes to GUI, TUI, API, service, observability, or manual evaluation. It uses
  a confirmed safe live experiment environment only within recorded allowed
  actions, uses passive production telemetry only for predefined operational
  gates with one exact grading rule, evidenced matching signal, and complete
  candidate/control attribution and coverage, admits only one query before
  updating its criterion ledger, and records planned slice observation when
  applicable.
- `sdlc-update-documents`: updates project-facing README, changelog, examples,
  usage docs, docs indexes, or generated docs after evaluated implementation or
  UAT evidence, while routing requirements and design drift to their owner
  skills. Multi-layer behavior docs require evaluated slice evidence.
- `sdlc-align-specs`: checks SDLC requirements, design, plans, tests,
  implementation, docs, end-to-end slice evidence, and other evidence for
  consistency before commit or PR readiness.
- `sdlc-classify-failure`: validates commit-bound failure events and optional
  diagnoses, enforces stable-blocker and feature budgets, and either routes a
  proven cause to its owner, conditionally requests troubleshooting, or stops.
- `sdlc-gui-test`: controls and evaluates GUI flows through Computer Use when
  desktop state matters, or Browser/Playwright when suitable, with screenshots
  or accessibility snapshots.
- `sdlc-tui-test`: controls and evaluates terminal, CLI wizard, or TUI flows with
  transcripts and exit-code evidence.
- `sdlc-commit`: seals final integration changes, ff-only promotes the exact
  verified tip, non-force-cleans integration resources, and never pushes or
  replaces the general `commit` or `commit-push` skills.
- `sdlc-uat-tests`: runs product-level user acceptance testing before PR
  creation, using a confirmed safe live experiment environment only within
  recorded allowed operations and reset rules.
- `create-pr`: existing PR skill reused in publication-only mode after UAT
  passes; it requires the clean exact promoted SHA, never changes it, and routes
  any repair back through failure classification and the coordinator. Push and
  CLI PR creation use one direct action with an explicit ref/head.
- `review-pr`: existing PR review skill reused in findings-and-readiness-only
  mode; it verifies the exact promoted PR head against specs, checks, reviews,
  and local evidence without mutating the branch.
- `sdlc-merge-pr`: merges a specific PR only after an explicit user request and
  final readiness checks, using one canonical single-action command with an
  explicit PR number or URL and exact head guard only while the PR head still
  equals the promoted and reviewed SHA.

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
One explicit invocation binds a private authorization, previews the complete
monorepo tree through a temporary index, then lets one digest-pinned helper run
repo-root `git add -A`, lightweight staged validation, normal hooks, and exact
direct-child/tree verification under the shared repository lock. Direct mode
requires the current selected-project lifecycle to be sealed or a bounded
fresh commit-only waiver, but never requires sibling-project attestations.
Repository-shaping Git environment is rejected before discovery or mutation;
only the transaction-owned preview may select a private index.
Task Implementer supplies exact worker ownership instead, while active Agentic
SDLC keeps `sdlc-commit`. The skill stops instead of pushing, creating PRs,
repairing branches, or writing Agentic SDLC evidence.

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
When a matching active Agentic SDLC run exists, that generic preparation path
is disabled: `create-pr` requires passing UAT and publishes only the clean exact
promoted SHA. A conflict, failed check, remote-head mismatch, or required
change returns through `sdlc-classify-failure` and `sdlc-start`.

### `worktree`

`worktree` creates full-repository children in a sibling
`<repo-name>-worktrees/` directory. `add` is the default action and requires the
complete primary checkout to be clean on a named non-default branch. It freezes
that exact local `HEAD` and creates a generated no-upstream child without
fetching. `--project` selects the returned starting directory and label only;
it does not restrict changed paths or staging.

When no task description is supplied, `add` normalizes the resolved project
directory basename as the task slug. From `skills/`, bare `$worktree` therefore
creates `project-skills-<6-hex>` on `feature/skills-<6-hex>` and returns the
child's `skills/` directory. Explicit task descriptions continue to produce
their own public-safe slugs.

After creation or exact reuse, Codex verifies the child from that returned
directory and adopts it for subsequent development commands. This does not
change a parent shell, the Codex workspace, or an editor window; editor
retargeting remains explicit, and lifecycle actions still run from the primary
checkout.

`integrate` runs only from the primary checkout. Its read-only preflight may
delegate one guarded whole-repository commit for an ordinary dirty child and
then one for a dirty source before freezing their exact clean heads. The first
commit creates a durable source-scoped preparation claim, and every resulting
commit tree must match the reviewed staged tree. Nested workflow participation,
competing attempts, orphan candidates, conflicts, Git operations, and unsafe
or unclear diffs block automatic commits. It builds one durable private
candidate, retains merge conflicts for recovery, exposes the exact candidate
for non-mutating combined validation, and fast-forwards the source checkout
only to that validated two-parent merge. Successful preparatory commits remain
local history if a later step fails. Direct managed-child push and PR creation
are rejected. Task Implementer and Agentic SDLC release into this explicit
primary-checkout handoff. `remove` remains a separate exact-proof action. The
skill never fetches for lifecycle decisions, force-removes, mutates remote
refs, rebases, or cherry-picks.

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

`code-info` summarizes a local project folder or GitHub repository with a
read-only, copy/paste-friendly Markdown report. It includes a concise project
description, documented feature count, CLI command paths through three levels,
comparable and per-language LOC, project packages, declared and statically
selected/resolved dependencies, approximate pinned Redis/SQLite size
comparisons, repo size and link, tests, artifacts, and already-available
coverage. For not-yet-cloned
GitHub repositories, it reads a temporary archive using `GH_TOKEN`,
`GITHUB_TOKEN`, or `gh auth token` when needed. It never executes project code,
manifests, package managers, builds, tests, or generators and does not change
project files.

### `config-codex`

`config-codex` bootstraps, recovers, or aligns a user's local Codex runtime
setup from public-safe templates. Use it for `$CODEX_HOME` layout, global
`AGENTS.md` policy, recovery of a missing `config.toml`, features and MCP
servers, hooks, task-state directories, custom read-only agents, and validation
without copying personal paths, private state, or secrets into a public
repository. The create-only config recovery baseline keeps documented portable
preferences and placeholders while excluding personal project lists, private
or plugin-managed integrations, desktop/generated state, and secret-bearing
values. Existing laptop `AGENTS.md` and `config.toml` files are merge targets,
not template replacement targets.
When a proven repo-owned guard falsely denies an authorized patch,
`config-codex` repairs its canonical source, validates and installs it through
the documented provenance path, reports restart/trust state, and retries the
identical edit. Alternate writers, shell redirection, installed-only edits,
guard disabling, and cwd escapes remain prohibited; manual action is reserved
for external or unrepairable controls.
Its compact global live-product policy freezes each trial declaration and
permits authorized recovery without weakening production or high-impact action
approval. Observation is classified by effect, and environment intervention
cannot become product proof: owner-correct repair and a clean replay from a
proven known-good point before the earliest product divergence or contamination
are required for a verified product-fix claim.
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
validation status, and next action. This non-SDLC setup owns its global-context
`SessionStart` and `UserPromptSubmit` registrations only. Agentic SDLC skills
and guardrails own their SDLC-specific `PreToolUse` and `Stop` registrations;
Nebius authentication and `troubleshoot` own separate peer registrations listed
in the [Source Hook Catalog](#source-hook-catalog).

### `gitignore`

`gitignore` creates or updates a project `.gitignore` with sensible macOS and
VS Code defaults, then extends it for the detected stack.

### `helmchart`

`helmchart` applies Helm chart best practices across metadata, values,
templates, schema, and validation.

### `install-grafana-mcp-for-nebius`

`install-grafana-mcp-for-nebius` installs and configures the official Grafana
MCP server for Codex. It pins and validates one human Nebius CLI profile,
stores a private profile/user binding, isolates rotating token state per
server/profile, renews stale startup credentials before MCP launch under a
bounded Codex timeout, clears agent and competing Grafana credentials, and
always enforces read-only MCP arguments. External Grafana
service-account/static-key setup remains separate. Routine observability
questions are handed to `nebius-grafana-query`; the installer retains no second
query path.

### `nosleep4mac`

`nosleep4mac` is an explicit-only, no-argument setup workflow for one per-user
macOS LaunchAgent running `/usr/bin/caffeinate -s`. Its canonical helper
creates missing state, repairs stopped or safely drifted managed state, and
leaves a healthy plist, PID, and launchd job exactly unchanged on repeated
runs. The assertion is effective only on AC power, continues through screen
lock while the user remains logged in, preserves normal display and battery
sleep, and does not change `pmset` or install a system daemon. The helper's
`--check` mode is reserved for internal verification and tests.

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

### `nebius-grafana-query`

`nebius-grafana-query` is the implicitly selectable, read-only operational
workflow for an already-configured `grafana-nebius` MCP server. It discovers
datasource UIDs and scope dimensions, resolves one absolute query window, and
proves federation before running bounded PromQL, LogQL, or Tempo queries as the
pinned human user. Requests can name one or many tenants, projects, and
resources; absent scope dimensions fail closed, and bare “any” does not grant
fleet-wide enumeration.

Results use a ranked, signal-aware Markdown report: explicit user grouping and
ordering win, otherwise the requested field is sorted problem-first with
stable scope attribution. The default table displays up to 20 rows with total,
omitted, and global-completeness notes. Gauges use per-group window minimum,
average, and maximum; counters use increase or rate; logs use counts and
exact first/last occurrence when a supported bounded method provides it;
traces use count/error, exact full-window duration minimum/average/maximum only
when supported, and supported full-window quantiles with approximation
disclosed. Coverage/access is separate from query and tool health.

Traces prefer proxied Tempo tools and use only a fixed GET-only
datasource-proxy fallback when those tools are unavailable. Missing server,
configuration, or authentication routes back to the explicit installer skill.

The same skill is the structured evidence provider for `troubleshoot` and
`sdlc-evaluate`. Those callers decide whether telemetry can change a named
hypothesis or operational criterion and supply explicit authority, deployed
selectors, absolute windows, one evidenced matching signal, and a query budget.
Evaluation also supplies one exact criterion fit with candidate/control
attribution and predefined pass, fail, and inconclusive rules. Invalid or
missing fits cause zero calls; datasource readiness is never signal discovery.
One lazy datasource listing is reused as readiness for the workflow run;
failure disables observability without installer handoff or retry. Each
embedded provider call attempts at most one admitted query, then returns for a
caller ledger update; the cumulative six-query fast and four-query deep
allowances are ceilings, not batch targets. The provider returns redacted facts
and data gaps without claiming root cause or an evaluation grade. Every
response returns updated connectivity and total, fast, and deep remaining
budgets;
pre-query validation gaps return `rejected` with zero calls and unchanged
state.

### `optimize-pytest`

`optimize-pytest` measures and improves pytest feedback time without weakening
test selection, outcomes, isolation, or the complete correctness gate. It
separates startup, collection, setup, call, and teardown costs, then ranks
cumulative contributors across fixtures, imports, parametrization, plugins,
coverage, reporting, and test boundaries.

The skill keeps review and diagnosis report-only, permits focused changes only
for explicit optimization requests, and evidence-gates xdist, testmon,
duration-balanced sharding, persistent artifacts, dependency changes, and
build-system escalation.

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
For a matching active Agentic SDLC run it instead becomes
findings-and-readiness-only: the PR head must equal the clean exact promoted
SHA, and every required branch change returns to the coordinator before review
resumes.

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

Before diagnostics, it discovers technologies and versions, deployment model,
active configuration sources, components and dependencies, ports, protocols,
authentication, and control and data flows, then compares the observed system
with matching official vendor architecture. It maintains a component
verification matrix and incident timeline, verifies clock synchronization, and
correlates component, application, container or orchestrator, systemd, OS,
kernel, network, storage, GPU, and hardware logs with bounded filters. Relevant
sources that cannot be examined are recorded as unavailable rather than
silently treated as healthy.

Technology procedures are progressively disclosed through Slurm, Soperator,
Kubernetes, Nebius, Linux, network, storage, GPU, and code-debugging playbooks.
Soperator guidance distinguishes dedicated, resource-consuming ActiveChecks
from passive prolog, epilog, and HealthCheckProgram checks around customer jobs.
Every consequential command states its hypothesis, expected and falsifying
evidence, timeout or output bound, and next branch; indefinite tails, arbitrary
sleeps, passive waiting, and large unfiltered log dumps are prohibited.

Every outcome uses one canonical report with architecture, component, timeline,
log, hypothesis, code-debugging, root-cause, remediation, and validation
evidence. Design, Infrastructure, Connectivity, Configuration, Runtime health,
Logs, and Relevant code paths each receive `PASS`, `FAIL`, or `UNKNOWN` with
evidence. `VERIFIED_FIXED` requires all seven to be `PASS`; passing tests alone
do not prove code is bug-free.

The report scopes its conclusions to explicit included and excluded system
boundaries, exercised paths, and an incident-window start and end. Component
proof names DNS and restart history, while the mechanically enforced log ledger
contains each of the eight component-through-hardware layers exactly once.

For live product validation, `troubleshoot` records causal ownership, target
recovery state, and evidence lineage separately and freezes each trial's
declared workflow. Authorized stabilization may restore a degraded target, but
an out-of-band mutation that performs, bypasses, or pre-satisfies a
product-owned step intervenes in the affected trial; so does nominally
read-only observation when it changes criterion-relevant state or execution. A
verified product fix requires an implemented owner-correct repair, a declared
or independently proven known-good checkpoint before the earliest product
divergence or contamination, quiescent prior writers, observed product-owned
transitions, and independent postconditions. Checkpoint replay proves only the
affected segment unless the full workflow is rerun.

Runtime observability is a gated experiment rather than a default discovery
step. When a deployed-runtime hypothesis needs scoped facts,
`troubleshoot` invokes `nebius-grafana-query` in evidence-provider mode after
one matching signal has non-Grafana provenance and authority, deployed
selector, and absolute window are proven. It reuses one readiness result,
admits one cheapest decision-changing query per provider call, enters deep
queries only when fast evidence leaves named hypotheses indistinguishable, and
retains all causal interpretation. Unavailable optional telemetry is skipped;
decisive missing telemetry becomes `BLOCKED_MISSING_EVIDENCE`.

After causal proof, `troubleshoot` keeps localized invariant-restoring repairs
inside its own workflow. Outside Agentic SDLC, a durable remedy that changes
architecture topology, component or service responsibilities or boundaries, a
public interface, data ownership or lifecycle, a migration, or a
cross-component workflow is routed through `design` before implementation.
Implementation size, algorithmic complexity, concurrency difficulty, or a
large rewrite inside one existing private boundary does not trigger `design`.
That handoff receives the proven causal chain, violated invariant,
requirements, constraints, non-goals, fixed technologies, and regression
oracle. `design` owns solution design and the `/plan` handoff;
`troubleshoot` retains post-implementation verification and final causal
reporting. Active Agentic SDLC work sends the causal handoff first to
`sdlc-classify-failure`; after it records the failure class, retry accounting,
and `next_recommended_skill`, the coordinator routes to the recorded owner.
In that diagnostic mode, `troubleshoot` preserves the failed integration commit
and accepted criteria, may use only reversible uncommitted instrumentation in
the private integration worktree, removes it before handoff, commits no product
repair, invokes no design phase, and returns one `diagnosis-v1` to
classification. A known mechanical owner bypasses this branch. Missing
decisive evidence, competing hypotheses, and “no implementation bug found”
stop without speculative redesign.

Agentic SDLC uses its own authoritative `repair-control-v1`: two localized
attempts, one design-scale attempt, three total attempts and 60 active minutes
per stable blocker tranche, plus four repair dispatches per feature. A failed
first direct repair requires troubleshooting before attempt two. Each retry
requires new evidence and a new falsifiable hypothesis; duplicates, permission
denials, and crash replay do not consume attempts.

After one remediation fails against the same blocker, `troubleshoot` starts a
bounded tranche before another repair. Each session defaults to five attempts
and 120 active minutes, with optional `$troubleshoot --attempt-limit=N` and
`--time-limit-minutes=N` flags capped at 10/180. A task-specific
earlier stop remains workflow guidance in prose and cannot be represented as a
free-text numeric override; it leaves the canonical marker active with a null
stop trigger and returns a normal structured report. Every retry requires newly acquired logs,
stack traces, code inspection, runtime-state evidence, or an equivalent
observation plus a genuinely new falsifiable hypothesis. If that gate cannot be
satisfied, no retry runs and a structured investigation report identifies the
missing evidence and next action. Each non-terminal failure is reported as
progress; after the exact private state update records exhaustion, other tool use
stops and a concise user-visible report is returned. An ordinary incomplete,
malformed, partial, `FAIL`, or `UNKNOWN` report is advisory: the Stop hook
records it without requesting another turn, denying later tools, or emitting a
generated fallback. Sensitive report content and exact remediation-budget
exhaustion retain one bounded correction and a redacted concise fallback. When
the hook supplies an exhausted-budget report, the assistant returns it verbatim
instead of rewriting its exact marker-derived fields. Historical exhausted v1 data
markers remain report-only without requiring invented evidence or authored
attempt IDs. Previous v2 and v3 state fails closed for exact marker repair rather
than continuing under a dual-limits compatibility path, while all newly authored
state uses the canonical v4 data schema. A new user instruction is required for
fresh state; an exhausted tranche cannot be reopened. The optional
`troubleshoot/assets/hooks` bundle enforces the
recorded private task-state budget at supported `UserPromptSubmit`, `PreToolUse`,
and `Stop` boundaries. Every canonical attempt is bound to the marker's exact blocker key,
so an old ledger copied onto an independent issue is rejected as invalid rather
than exhausting that issue. The hook does not infer causal identity or attempts
from raw command failures. Attempt entries are appended only after remediation
and verification complete; planned or in-progress work remains in prose with
an empty ledger. A malformed partial entry receives one atomic remove-or-
complete repair instruction listing all missing canonical fields.

## Source Hook Catalog

The current source tree contains seven installer-discovered hook bundles. Their
16 manifest entries converge to 13 distinct registrations backed by nine
manifest-referenced Python entrypoints because four bundles carry the same
single-Stop arbiter entry. This is a current-tree inventory, not a fixed allowlist:
`--install-all-hooks` discovers each direct child skill that contains both
`SKILL.md` and `assets/hooks/`.

| Registration | Source entrypoint | Description |
| --- | --- | --- |
| `SessionStart` matching `startup\|resume\|clear\|compact` | `global-context-management` [`session_start_context.py.template`](global-context-management/assets/session_start_context.py.template), installed from its byte-identical `config-codex` mirror | Resolves the workspace root and advertises the session-scoped private `current.md` path. It secures existing task-state permissions and creates an empty `0700`/`0600` scaffold only after compaction. Missing session information or unsafe initialization degrades to unavailable context instead of blocking the session. |
| `SessionStart` matching `startup\|resume\|clear\|compact` | `maintain-project-specs` [`project_specs_lifecycle.py`](maintain-project-specs/assets/hooks/project_specs_lifecycle.py) | Performs a bounded audit of owner-only lifecycle state across prior sessions for the selected project and injects recovery guidance for unfinished phases. It does not author requirements, design, or repository instructions. |
| `UserPromptSubmit` for all prompts | `global-context-management` [`user_prompt_context.py.template`](global-context-management/assets/user_prompt_context.py.template), installed from its byte-identical `config-codex` mirror | Emits nothing for a simple prompt. For a complex prompt, it creates or secures the empty task-state scaffold, suggests a bounded set of related same-workspace state-file paths without injecting their contents, and adds context-management guidance. A local opt-in policy may also request bounded read-only subagents. |
| `UserPromptSubmit` for all prompts | `commit` [`commit_intent.py`](commit/assets/hooks/commit_intent.py) | Recognizes optional `please`, then either a root-user `$commit` directly or a bounded leading directive from `run`, `apply`, `execute`, `invoke`, or `use`, while excluding casual mentions, questions, quotations, help, subagents, system turns, compaction, and Stop continuations. It stores only current repository/session/turn/prompt digests and owner metadata in an owner-private authorization, then injects the canonical authorization and claim paths; it never stages or commits. |
| `UserPromptSubmit` for all prompts | `troubleshoot` [`remediation_attempt_guard.py`](troubleshoot/assets/hooks/remediation_attempt_guard.py) | Parses only an exact leading `$troubleshoot`, authorizes default or explicitly bounded attempt/time limits, records the private authorization sidecar, and establishes a separate same-session terminal-report obligation without storing the prompt. It supplies bounded profile or repair context and blocks invalid authorization transitions. |
| `UserPromptSubmit` for all prompts | `maintain-project-specs` [`project_specs_lifecycle.py`](maintain-project-specs/assets/hooks/project_specs_lifecycle.py) | Binds the selected project and current turn, carries unfinished implementation or sealing state across follow-up prompts, and injects the requirement/design planning and bounded waiver contract. Only an exact committed project-policy blob may disable the lifecycle. |
| `UserPromptSubmit` for all prompts | `prompt-session-intake` [`prompt_session_intake.py`](prompt-session-intake/assets/hooks/prompt_session_intake.py) | Binds only exact Task Implementer or Agentic SDLC init/run invocations. Later direct turns always pass to the current agent; eligible safe input stages metadata-only event-v2 session/turn causality and never a prompt body. The current agent records merge/no-op/sensitive and only a durable project-intent projection may reach the canonical prompt through an operation-and-projection-bound adapter. Secrets, stale provenance, conflicts, unsafe state, ambiguity, and internal capture errors skip persistence without stopping delivery. Unbound, Stop-generated, compaction, system, and subagent prompts do not stage. The hook never edits a workflow prompt or starts a run. |
| `PreToolUse` matching `^Bash$` | `agent-nebius-auth-setup` [`pre_tool_use_nebius_auth.py`](agent-nebius-auth-setup/assets/hooks/pre_tool_use_nebius_auth.py) | Activates only for Nebius-sensitive Bash commands. It resolves the explicit task project or sanitized default profile, validates the canonical owned mode-`0600` credential, rejects token or environment disclosure and conflicting auth state, and rewrites an allowed command with renewable project/profile/credential context. Unrelated Bash commands pass unchanged; relevant unsafe or internally failing cases are denied. |
| `PreToolUse` matching `Bash\|apply_patch\|Edit\|Write\|mcp__.*` | `sdlc-start` [`pre_tool_use_sdlc_policy.py`](sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py) | Applies policy only when an active Agentic SDLC run covers the working directory. It blocks dangerous shell patterns, secret-bearing payloads, unauthorized Git, GitHub, merge, and MCP actions, can warn about spec-phase drift, and records private hook history. No active run passes immediately; corrupt active state or internal failure denies the tool call. |
| `PreToolUse` matching `*` | `troubleshoot` [`remediation_attempt_guard.py`](troubleshoot/assets/hooks/remediation_attempt_guard.py) | Validates the parent-authored remediation marker and its authorization handshake. A normally missing marker passes. Pending, invalid, exhausted, or terminally locked state blocks tool use except for an exact `apply_patch` that updates only the advertised `current.md`. Ordinary report-quality gaps never deny tools; only an active sensitive-output redaction correction remains a report-related tool boundary. |
| `PreToolUse` matching `Bash\|apply_patch\|Edit\|Write\|mcp__.*` | `maintain-project-specs` [`project_specs_lifecycle.py`](maintain-project-specs/assets/hooks/project_specs_lifecycle.py) | Requires current planning and a verified project-rule render before selected-project implementation, protects canonical specs and lifecycle-owned `${CODEX_HOME}/project-specs` state, recognizes non-symlinked coordinators from canonical `~/.agents/skills`, binds ordinary project-instructions evidence to the exact current-session private bundle, and parses shell quoting before classifying effects. The sole alternate bundle admits exact Task Implementer run-owned inspect and render during reconciliation-required, authenticated by a canonical installed or sibling-source helper for the active prepared integration checkout; the hook rechecks its command digest and selected outer project. Apply and verify retain the ordinary terminal seal path. Proven fixed writes to config, hooks, task state, installed skills, credentials, and other external user files pass through epoch-neutral to their actual policy owners. Mixed, dynamic, ambiguous, selected-project, malformed coordinator-shaped, unattested alternate-bundle, or lifecycle-private effects retain their gates. An exact digest-pinned commit transaction requires a sealed/waived direct lifecycle or Task Implementer owner evidence; raw Git stays denied. Exact current-session runtime/decision authoring, private-input mode tightening, and canonical spec intent-to-add break bounded bootstrap cycles without opening lifecycle state or Git-index mutation. Multiple implementation edits remain open after the first write. |
| `PostToolUse` matching `Bash\|apply_patch\|Edit\|Write\|mcp__.*` | `maintain-project-specs` [`project_specs_lifecycle.py`](maintain-project-specs/assets/hooks/project_specs_lifecycle.py) | Silently marks a successful material selected-project write as reconciliation-required and advances the compare-and-swap write epoch. Concurrent recorders converge; a late success after planning or sealing invalidates that later evidence and reopens reconciliation. The first authenticated Task Implementer `wave-plan` similarly records its successful run-owned lane checkpoint before dispatch; failed or unbound commands cannot claim it. Proven external effects, exact bootstrap transitions, admitted canonical spec reconciliation, and a completed digest-pinned commit prepare with its exact consumed authorization and claim remain epoch-neutral. Recording errors and invalid completed coordinator-shaped calls stay visible. After independently verifying the canonical current-session terminal project-instructions apply, it advances to `seal-armed`. It cannot undo a completed side effect. |
| `Stop` for all stops | shared [`stop_lifecycle_arbiter.py`](maintain-project-specs/assets/hooks/stop_lifecycle_arbiter.py), carried byte-identically by `maintain-project-specs`, `prompt-session-intake`, `sdlc-start`, and `troubleshoot` | Runs the troubleshooting, project-contract, SDLC, and prompt-session Stop evaluators sequentially within one 25-second monotonic budget below the registered 30-second host timeout. A terminal result takes precedence; otherwise every initial continuation reason is combined. Each explicit troubleshoot turn should supply the concise outcome, cause/fix, verification, and next-action report. Valid delivery is finalized transactionally only after no peer continuation remains; ordinary report-quality gaps record an advisory result and never request continuation or emit a fallback. Sensitive output and exact remediation-budget exhaustion retain bounded fail-closed reporting. Project reconciliation still requests one accumulated semantic review and a conditional project-instructions decision; verified `not-needed` leaves a missing file absent. Prompt-session cleanup always passes Stop and best-effort releases writer provenance; incomplete or invalid capture never requests continuation. Missing managed project state fails closed for its owning delegate. Legacy independently registered Stop entries are migrated only when an exact singleton managed command is proven. |

Matching registrations are independent. Codex starts matching command hooks for
the same event concurrently, so they must not depend on ordering or
short-circuiting. In this catalog, up to five `UserPromptSubmit` handlers, four
Bash `PreToolUse` handlers, three `apply_patch` `PreToolUse` handlers, and one
`PostToolUse` handler can match one event. `Edit` and `Write` are matcher aliases for
canonical `apply_patch`, not additional entrypoints. Stop policy is different:
one registered arbiter calls its available delegates in deterministic order.
For `Stop`,
`decision: "block"` requests another assistant turn, while `continue: false`
terminates and takes precedence over peer continuation decisions. See the
[official Codex Hooks documentation](https://learn.chatgpt.com/docs/hooks).

The manifests live in each owner skill's `assets/hooks.json.template`; the
global-context manifest and payloads are mirrored under
`config-codex/assets/hooks/` for installation. Helper modules, policy JSON,
tests, and [`install-skills.sh`](install-skills.sh) are supporting payloads or
installation machinery, not additional event handlers. Source validation does
not prove installed or in-memory activation: hook installation and registration
are explicit, changed hooks require a Codex restart, and non-managed hooks must
be reviewed and trusted in `/hooks`.

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
The `--install-all-hooks` option is also explicit, but discovers every direct
child skill-owned `*/assets/hooks` directory under this source skills folder
and syncs those payload files in one pass. It does not scan mixed `assets/`
directories.
With `--register-hooks`, it also merges each discovered bundle's registration
manifest while preserving existing hook entries. Add
`--refresh-hook-registrations` to replace only differing registrations with the
same event/script and handlers, allowing only `statusMessage` metadata to
differ. Add `--replace-hooks-json` only when you intentionally want to back up
and replace `hooks.json` with a clean file built from the selected source
manifests. Hook install modes are
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

# Copy and refresh optional Agentic SDLC hooks in the default local Codex home
./install-skills.sh --install-hooks sdlc-start/assets/hooks --register-hooks --refresh-hook-registrations

# Copy and register global context-management hooks
./install-skills.sh --install-hooks config-codex/assets/hooks --register-hooks

# Copy and register the remediation-budget guard hooks
./install-skills.sh --install-hooks troubleshoot/assets/hooks --register-hooks

# Copy and refresh every discovered hook-only bundle
./install-skills.sh --install-all-hooks --register-hooks --refresh-hook-registrations

# Copy all discovered hook bundles and replace hooks.json with only those entries
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
  file names, and syncs all discovered hook bundles into
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
  containing every discovered hook bundle under this source folder.
- Hook install modes report extra files under
  `${CODEX_HOME:-$HOME/.codex}/hooks` and extra `hooks.json` registrations that
  are not present in the selected source manifests. These reports are advisory:
  review the entries and remove obsolete files or JSON entries manually.
- Hook registration does not trust hooks. Restart Codex and review/trust new or
  changed hook entries in `/hooks`.
