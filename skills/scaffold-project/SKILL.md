---
name: scaffold-project
description: "Coordinate creation of repositories and components, including Python-only projects, with specialist-owned candidates, idempotent apply, and Python readiness. Use for scaffold creation or topology changes; route existing-project maintenance to its specialist. Not for stack selection, feature logic, builds of existing packages, or SDLC."
---

# Scaffold Project

## Help

For `$scaffold-project --help` or `$scaffold-project -h` (including native Claude forms), return concise help and stop before
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

Use `$scaffold-project` in Codex, `/scaffold-project` in Claude Code, or
`/skills:scaffold-project` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Convert an approved architecture into a repository topology and coordinate its
safe creation without duplicating language, framework, infrastructure,
deployment, CI, ignore-rule, or shell specialist ownership.

## When To Use

- A natural-language or explicit skill request creates a repository or component,
  including a Python-only package, CLI, API, or systemd service scaffold.
- The user requests additive repository topology standardization.
- An existing design/app-stack decision is ready to materialize within the
  user's creation task. A separate skill invocation is unnecessary.

## When Not To Use

- Existing-project dependency, test, packaging, lint, or configuration work:
  use the matching specialist directly. Building an existing wheel is not
  creating a new project.
- Material architecture or technology choices remain undecided: return the
  missing decisions to the owning design/app-stack workflow without recursion.
- Product feature implementation, destructive restructuring, or Agentic SDLC.
- A required artifact lacks a supported specialist producer contract.

## Invocation Policy

Implicit and explicit invocation are supported. Interpret intent before effects:

- Plan, explain, review, help, and quoted examples do not authorize target or
  environment changes. Host Plan Mode also prohibits private plan writes.
- A create/build-new/apply request authorizes bounded candidate generation,
  validation, guarded apply, and required Python setup in the same task. Do not
  ask for a second approval, magic phrase, or user-supplied digest.
- Preserve an explicit files-only or offline constraint; report readiness as
  pending. Ask only about material missing decisions or effects outside scope.
- Finalize and pass the exact digest internally as an integrity binding. A
  digest is not an authorization receipt.
- For Python creation, use its established defaults for routine unspecified
  choices; do not invent an extra architecture-approval step.

Check invocation scope before intent routing. Python creation hands ownership
here once; calls to its coordinated-candidate and bootstrap phases never route
back to this entrypoint. See [routing](references/creation-routing.md).

## Inputs

- Target project directory.
- Selected architecture or direct-user creation reference.
- Project identity and repository shape.
- Logical capabilities with `required`, `conditional`, `deferred`, or
  `rejected` status.
- Physical materialization units, runtime units, external services, and
  cross-cutting artifacts.
- Package, module, workspace, image, chart, and workflow identities required by
  the selected specialists.
- Runtime and deployment decisions that change generated files.
- Requested mode: plan, finalize, validate, status, or apply.
- Private bundle directory when the advertised private task-state location is
  unavailable.

## Required Reads

1. Read every applicable repository instruction file from the repository root
   through the target.
2. Inspect the target, Git state, existing files, symlinks, nested instruction
   files, manifests, and nearby conventions.
3. Read `references/scaffold-contract.md` for every plan or apply.
4. Read `references/ownership-routing.md` before selecting specialists.
5. Read `references/question-policy.md` when required inputs are incomplete.
6. Read `references/layout-patterns.md` before assigning component paths.
7. Read `references/safety-model.md` before finalizing or applying a bundle.
8. Read only the matching specialist skill instructions and their required
   references. Respect stricter specialist guardrails.

## Writes

- Planning may write only a private `0700` bundle containing `0600` draft,
  candidate, payload, manifest, journal, and backup files.
- Coordinated specialists may write exact candidate bytes only into that
  private bundle.
- The guarded executor is the only writer of scaffold content. After apply,
  Python bootstrap may write declared dependency/environment/build artifacts
  within the same creation task; it never rewrites authored scaffold files.
- A sanitized `docs/scaffold-plan.json` may be proposed only when the user
  explicitly requests an auditable committed copy. It is never the executable
  plan and must not contain secrets, local absolute paths, or private context.

## Process

### 1. Establish Scope

- Treat a non-empty target as brownfield unless the user explicitly supplies a
  narrower component-addition scope.
- Classify safety per path. New component files may be greenfield while root
  README, ignore, workflow, or workspace files remain brownfield.
- Refuse targets outside the declared workspace or whose parent is a symlink.
- Ask one batch of decision-changing questions only when the answer cannot be
  discovered. In non-interactive mode, return missing fields instead of
  guessing.

### 2. Normalize The Scaffold Handoff

- Keep logical capabilities, materialization units, and runtime units
  separate.
- Allow multiple capabilities, such as API and worker, to share one source
  materialization unit while retaining separate runtime units.
- Treat databases, caches, queues, and managed services as external services;
  do not create source folders for them.
- Materialize only `required` capabilities. A `conditional` item must first
  satisfy its documented trigger and become `required`. Never create
  placeholders for `deferred` or `rejected` items.
- Bind approved architecture source paths and digests when an artifact provides
  the approval. Direct-user approval may use an empty source list.
- When the approval source is an app-stack scaffold handoff, validate its
  closed logical-only schema and bind its exact digest. Require every approved
  required component to retain its status and exact technology selection
  through a scaffold capability or external-service binding. Required
  materialized components also bind the canonical technology name and language
  to every assigned unit, and bind every runtime unit to one capability.
  Accept the closed Python and frontend capability contracts; reject other
  non-frontend capability selections until their specialist exposes a contract. Derive repository topology locally; do not
  accept physical paths or owners from that handoff.

### 3. Assign Ownership

- Assign exactly one content owner to every planned file.
- Reserve `AGENTS.md`, `AGENTS.override.md`, and configured instruction paths
  from all producers.
- Use only these owners:
  `scaffold-project`, `python-project`, `frontend-project`,
  `container`, `terraform`, `helmchart`, `github-workflows`,
  `gitignore`, and `shell-scripting`.
- Require every path to match its owner's positive artifact contract. Stop
  before candidate generation if any artifact is unsupported or any path has
  duplicate, ancestor, Unicode-normalized, or case-folded ownership.
- The canonical container owner identifier is `container`. Private bundles
  finalized with an earlier owner identifier must be regenerated; do not
  translate or resume them.

### 4. Collect Exact Candidates

Invoke applicable specialists in `coordinated-candidate` scope. Give each
specialist its assigned paths, approved inputs, exclusions, ownership map, and
private bundle location. Specialists return exact candidate bytes plus a
closed manifest containing the profile, normalized inputs and digest, per-file
digests, and candidate/post-apply validation records without writing the
target. For app-stack-approved React/Vite components, finalization compares
those normalized inputs with the logical technology decision.

Do not run native generators, package managers, dependency installation,
provider downloads, image builds, Git initialization, or network tools during
candidate generation. Python bootstrap runs only after static application. No native generator
may bypass the candidate producer or guarded executor.

### 5. Review Brownfield Merges

- `create`: target must be absent.
- `unchanged`: target bytes and mode must already equal the candidate.
- `semantic_merge`: target must be a regular file and the candidate must
  preserve existing content while adding only approved integration material.
- Permit semantic merge candidates only for:
  - additive `.gitignore` rules that retain existing rules and comments;
  - isolated, uniquely marked README or Makefile sections;
- Schema v2 enforces these as an exact byte-preserving, non-empty suffix append to
  `.gitignore`, `README.md`, or `Makefile`, with the original mode unchanged.
  `.gitignore` additions must be unique rules from the reviewed `gitignore`
  baseline/add-on allowlist or the exact rules derived from validated Python
  candidates in this bundle. README and Makefile additions must contain one
  matching named `scaffold-project:begin`/`end` marker block. Structured-key
  merges are unsupported until an owner-specific parser and preservation
  validator exists.
- Do not splice existing source code or workflow files automatically. Create a
  separate non-conflicting file or report a collision.
- Present the complete operation list and every semantic merge diff before
  finalization.

### 6. Finalize And Validate

Create `manifest.draft.json` and candidates under the private bundle according
to `references/scaffold-contract.md`, then run:

```bash
python3 scaffold-project/scripts/scaffold_project.py finalize \
  --target <target> \
  --bundle <private-bundle>

python3 scaffold-project/scripts/scaffold_project.py validate \
  --target <target> \
  --bundle <private-bundle>
```

Finalization copies candidates into content-addressed payloads, records exact
target preconditions, verifies candidate-set manifests and validation binding,
canonicalizes operation order, and emits the digest-bound `manifest.json`. If
any candidate, candidate set, path, owner, architecture source, or precondition
is invalid, stop without target writes.

### 7. Review The Concrete Operations

Return the proposed tree, owner routing, exact operations, semantic merge
diffs, bundle digest, validation commands, commands not run, and all pending
network or external evidence. For an authorized creation task, continue directly with that exact digest.
For plan-only requests, stop with the proposed operations; do not apply.

### 8. Apply And Resume

Apply the internally finalized digest with:

```bash
python3 scaffold-project/scripts/scaffold_project.py apply \
  --target <target> \
  --bundle <private-bundle> \
  --expected-digest <sha256>
```

The executor performs an all-path preflight, rejects symlinks and special
files, publishes an absent root through a retained staging descriptor and
platform-native atomic no-replace rename, rechecks identity and digest before
every operation, uses no-follow descriptor-relative writes, keeps brownfield
backups in private state, and updates a crash-safe journal. It never deletes a
target path. If the platform or filesystem lacks the required no-replace
primitive, apply fails closed.

Use `status` to classify each operation as before, after, or conflict:

```bash
python3 scaffold-project/scripts/scaffold_project.py status \
  --target <target> \
  --bundle <private-bundle>
```

On interruption, rerun `status`. Resume only when every path is still at its
recorded before or after state. Never auto-rollback a partial apply.

### 9. Complete And Validate The Result

- Confirm the actual tree matches required materialization units.
- Confirm conditional, deferred, and rejected items were not created.
- Run owner-provided static validation and local tests.
- For each core Python component, run its bootstrap action after apply under
  the same creation authorization. It resolves a real uv.lock, syncs the owned
  environment, runs lint/tests and installed-wheel checks, and records separate
  readiness. A failure means applied but not ready; revalidate and resume setup.
- Provider downloads, Docker builds, provisioning and service activation stay
  outside default creation. Report platform checks separately.
- Invoke `$align` in audit-only mode. If it proposes an in-scope content change, regenerate the affected
  bundle and validate its new digest under existing task authority; never patch
  authored scaffold bytes behind the executor.
- Never automatically start Agentic SDLC. The user may invoke it separately
  after scaffold validation.

## Idempotency

- Canonical JSON, sorted normalized paths, and content-addressed payloads make
  identical final plans digest-stable.
- Reapplying a completed bundle leaves matching files and mtimes unchanged.
- Existing after-state files are classified as already applied during resume.
- A changed architecture source, manifest, payload, target identity, file mode,
  or before digest invalidates the operation rather than creating a fallback
  path.

## Failure Handling

- Missing architecture decision: return it to the upstream owner and stop.
- Unsupported owner or known collision: block finalization and all writes.
- Preflight conflict: make zero target writes.
- Mid-apply drift: stop immediately, mark the journal `partial`, preserve
  completed writes, and require status/review before resume.
- Interrupted semantic merge: retain the original bytes in the private backup
  store and report the path; do not restore automatically.
- Unsupported platform: allow plan, finalize, validate, and status; block apply.
- No safe private bundle location: remain plan-only.

## Must Not

- Do not select or reopen the application stack.
- Do not call `design`, `app-stack`, Implement,
  `project-agent-instructions`, or any `sdlc-*` skill from this workflow.
- Do not let specialists write the target in coordinated mode.
- Do not delete, blindly overwrite, silently skip, or create a compatibility
  plan format.
- Do not initialize Git, install dependencies, access secrets, call network
  generators, create remotes, provision, deploy, publish, commit, push, or open
  a PR.
- Do not place executable plans, journals, backups, transcripts, secrets, or
  local absolute paths in the generated repository.
- Do not claim end-to-end runtime readiness from structural validation alone.

## Completion Criteria

- Every required materialization unit and file has one supported owner.
- The schema-v2 contract, candidate manifests, normalized input digests,
  validation bindings, payload digests, architecture inputs, target
  preconditions, and overall bundle digest validate.
- Approved operations are applied or classified unchanged with no conflict.
- The resulting tree matches the approved plan and contains no deferred,
  rejected, instruction, secret, or local-path artifacts.
- Owner validation and audit-only alignment are complete, with unavailable
  external checks reported as pending.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Mode, target, repository shape, and approval source.
- Logical capabilities, materialization units, runtime units, and statuses.
- Proposed or actual tree.
- Path-to-owner routing and unsupported/blocking paths.
- Create, semantic-merge, and unchanged operations.
- Private bundle location and digest without exposing its contents.
- Validation run, pending external checks, and audit-only alignment result.
- Apply or recovery status, conflicts, and next safe action.
