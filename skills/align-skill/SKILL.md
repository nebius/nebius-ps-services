---
name: align-skill
description: "Align and harden existing/scaffolded Codex or Agent Skill folders: triggers, concise instructions, metadata, safety, resources, and evals. Accept local or report-only GitHub sources; use skill-creator for new scaffolds and align for projects."
---

# Align Skill

## Help

For `$align-skill --help` or `$align-skill -h`, return concise help and stop before
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

Inspect, align, harden, validate, and improve one or more existing Codex or
Agent Skill folders. Make the target easier to trigger correctly, cheaper to
load, safer to execute, and independently evaluable. For every authorized
writable target that completes alignment, create or update its canonical
trigger evals and report static, runtime, and output-quality evidence
separately.

This skill is separate from `align`, which is for end-to-end project/codebase
alignment.

## Use This Skill For

- Aligning a named skill, local skill folder, multi-skill parent folder, GitHub
  repository URL, or GitHub tree URL.
- Helping refine, harden, or complete a skill draft or newly scaffolded skill
  folder after the target scope exists.
- Reviewing or fixing `SKILL.md` front matter, description trigger quality,
  scope, workflow clarity, output contract, and progressive disclosure.
- Standardizing skill folders against a canonical skill structure.
- Adding or repairing the standard `## Learning Loop` rule on target skills.
- Adding or repairing the standard side-effect-free `## Help` contract so
  `$skill-name --help` and `$skill-name -h` concisely explain the skill and
  describe every public action, positional argument, and flag.
- Reviewing trigger/evaluation prompts stored under `evals/`.
- Creating or updating target-specific trigger evals for an authorized writable
  skill and selecting proportionate output-quality or deterministic tests.
- Checking skill guidance, commands, scripts, examples, and vendor-specific
  claims against current official documentation.
- Adding or hardening safety guardrails before validation or live tests.
- Applying `code-review` and `apply-security` review lanes to every target
  skill before claiming the target is aligned.
- Applying the optional stateful-workflow skill profile for skills that manage
  local state, locked plans, evidence, continuation, retries, or failure
  routing.

## Inputs Accepted

- Skill names available in the current workspace or installed skill paths.
- Local paths to one skill folder or a parent folder containing multiple
  skills.
- GitHub repository URLs or GitHub tree URLs that contain one or more skills.
- User-provided constraints such as "report only", "do not run live tests", or
  "only update `SKILL.md`".

## Non-Goals

- Do not use this for general codebase alignment; use `align` for that.
- Do not replace `skill-creator` for initial scaffolding when the user is
  creating a brand-new skill and the creator is available. Use this skill as the
  authoring, hardening, and validation helper around that flow.
- Do not broaden a skill until it becomes hard to trigger correctly.
- Do not rewrite skills from vague "best practices" without evidence.
- Do not mutate report-only, remote-only, or otherwise unauthorized targets;
  report their alignment and eval gaps as partial.
- Do not run live external changes unless a non-production test environment is
  confirmed.

## Triggering This Skill

Codex uses progressive disclosure: it sees skill metadata first, then loads
the full `SKILL.md` only when the skill is selected. Front-load target type,
authoring intent, and boundaries in the front matter `description`.

For deterministic activation, mention `align-skill` plus the target path,
skill name, folder, GitHub repository URL, or GitHub tree URL. In ChatGPT, type
`@` to select a skill. In Codex CLI or the IDE extension, use `/skills` or type
`$` to mention a skill.

Read `references/triggering-guide.md` when reviewing trigger behavior, surface
support, or prompt examples.

## Alignment Principles

- Evidence-based changes only: use repo evidence, official vendor
  documentation, or explicit user requirements.
- Distinguish the OpenAI portable baseline from repository-specific policy.
  The upstream baseline is a skill folder with `SKILL.md` containing `name` and
  `description`; `agents/openai.yaml`, `assets/`, `evals/`, `references/`, and
  `scripts/` are optional unless local repository policy requires them.
- Continuous source learning: before completion, update the locally cloned
  source materials associated with the target skill with durable, reusable
  knowledge discovered during the run. Capture patterns, decisions, best
  practices, and relevant findings only when they are evidence-backed,
  public-safe, and in scope.
- Keep skills scoped to their actual job and keep `SKILL.md` short enough for
  progressive disclosure.
- Prefer directives over essays. Preserve rationale only when it changes a
  decision, constraint, route, validation, safety outcome, or output; remove
  text whose absence would change none of those.
- Calibrate freedom per workflow block: goals and constraints when several
  approaches are safe, bounded defaults when one route is preferred, and a
  tested script or exact sequence when behavior is fragile or deterministic.
- Treat more than 500 `SKILL.md` lines as a review warning. Move deep material
  into focused references or record why the remaining core is justified.
- Move large checklists, templates, policies, and long examples into
  `references/` or `assets/`.
- Keep target `SKILL.md` content limited to trigger, scope, required workflow,
  guardrails, validation, and output contract. Put detailed rubrics, examples,
  policy, troubleshooting, and templates in supporting files.
- Scripts should be self-contained where possible, have helpful errors, avoid
  network calls unless explicitly needed, and fail safely.
- Do not persist secrets, private URLs, customer data, raw logs, or one-off
  environment details into reusable skill sources.

Read `references/canonical-skill-structure.md` and
`references/alignment-rubric.md` when structure or quality criteria are in
scope.

Read `references/progressive-disclosure-refactor.md` before editing when a
target exceeds 500 `SKILL.md` lines or semantic review finds its always-loaded
instructions overloaded. Do not summarize sections mechanically.

Read `references/evaluation-guide.md` whenever creating, migrating, validating,
running, or reporting target evals.

For skill draft, scaffold, or update tasks, also read
`references/skill-authoring-best-practices.md` after the target scope is known.
For coordinator or state-machine skills, also use
`assets/stateful-workflow-skill-template.md` as the optional section template
and validate with the `stateful-workflow` profile when appropriate.

## Skill Authoring Helper Workflow

When the user asks to refine, harden, complete, or update a skill draft or
newly scaffolded skill folder:

1. Confirm whether the task is a brand-new skill, an update to an existing
   skill, or a review of generated skill content.
2. If creating a brand-new skill and `skill-creator` is available, use it for
   the initial scaffold or naming workflow, then return here for alignment,
   safety, trigger quality, and validation.
3. Start from concrete use cases and create or update the target's canonical
   trigger CSV with at least three should-trigger and three meaningful
   near-miss should-not-trigger prompts.
4. Keep the skill focused on one repeatable job and front-load the `description`
   with user intent, accepted inputs, and boundaries from adjacent skills.
5. Apply the correct structure profile:
   - OpenAI portable minimum: `SKILL.md` with front matter `name` and
     `description`.
   - OpenAI optional metadata: `agents/openai.yaml` for UI metadata, invocation
     policy, and tool dependencies.
   - This repository's source-owned standard: keep `agents/openai.yaml` on
     every skill, keep the standard `## Help` and `## Learning Loop` sections,
     require canonical trigger evals after writable alignment, and add other
     resource folders only when useful.
6. Inventory the target's documented public actions, positional arguments, and
   flags. Add or repair the standard `## Help` contract so every public item
   has exact usage plus a concise description. Include `-h, --help`; when the
   skill has no other public flags, require the help response to say so. Never
   infer public options from private helper scripts or workflow transitions.
7. Create or repair `agents/openai.yaml` when the target repository convention
   expects OpenAI metadata. Use `agents/openai.yaml`, not `agents.openai.yaml`.
   Add `interface.default_prompt` and a `policy.allow_implicit_invocation`
   value derived from the skill requirements and `SKILL.md`.
8. Apply lean, safe, secure, and fast skill guidance from
   `references/skill-authoring-best-practices.md`.
9. Select output-quality evals or deterministic tests proportionate to the
   target change, following `references/evaluation-guide.md`.
10. For stateful workflow skills, add explicit `Required Reads`, `Writes`,
   `Idempotency`, `Failure Handling`, `Must Not`, and `Completion Criteria`
   sections. Keep private execution state out of committed project files and
   keep hooks as invariant guardrails rather than workflow orchestrators.
11. Run the mandatory review lanes for the target skill scope, then validate
    locally with `--require-evals` and the narrowest relevant checks. Broaden
    only when the contract or shared validator changed.

## Help Interface Enforcement

For every repo-owned skill that is created, refined, or aligned, add or repair
a standard `## Help` section. Treat `$skill-name --help` and `$skill-name -h`
as report-only requests that, after the selected `SKILL.md` loads, stop before
workflow reads, additional tools, or mutation.

Before writing the Help contract, inventory the documented public interface.
Help must state the skill's purpose and invocation policy, show exact usage for
every public action, and describe each public action, positional argument, and
flag in one concise line. It must include `-h, --help`; when no other public
flags exist, it must say "No additional public flags." Use only the documented
public interface. Never expose private helper actions, script flags, or
workflow transitions. If the public interface is ambiguous, resolve the skill
contract or report the unresolved gap instead of inventing an option.

Internal or coordinator-only skills must state that boundary and that they have
no standalone public workflow action.

## Learning Loop Enforcement

When aligning target skills, inspect each target `SKILL.md` for a
`## Learning Loop` section. Add the standard section when it is missing,
keep existing wording only when it contains the validator-required public-safe
snippets, and repair stale or unsafe variants that allow raw logs, secrets,
environment-specific details, or unverified vendor claims. Report which target
skills were updated, already compliant, skipped, or left uncertain.

## Evidence and Vendor Verification

For every product, framework, SDK, CLI, API, or cloud service used by the target
skill, check current official vendor documentation before changing related
guidance, commands, examples, or code. Prefer official docs over blogs,
tutorials, generated examples, Stack Overflow, or memory.

If official documentation does not verify a vendor-specific behavior, mark it
as unverified instead of presenting it as fact. Read
`references/vendor-research-policy.md` for the full policy.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Mandatory Review Lanes

Before reporting a target skill as aligned, apply these lanes to every target
skill selected by a single-skill path, multi-skill parent folder, GitHub
repository URL, or GitHub tree URL:

- `code-review` in review-only mode for instruction quality, support scripts,
  validation gaps, maintainability, over-complexity, and bloated `SKILL.md`
  content.
- `apply-security` in advisory or scan mode for secrets, private URLs, unsafe
  live actions, credential handling, external writes, dangerous scripts, and
  supply-chain risk.

These lanes do not broaden `align-skill` into project-wide `align`. Keep them
scoped to the target skill folders and their directly referenced resources. In
report-only mode, both lanes stay report-only. If edits are allowed, apply only
focused, low-risk fixes already permitted by the active task and by each child
skill's stricter safety rules.

If either lane cannot be resolved, loaded, or completed, report the lane as
incomplete with the paths or skills checked. Do not claim full alignment until
blocking `code-review` or `apply-security` findings are fixed, explicitly
deferred by the user, or reported as owner-review required.

## Safety Guardrails

Add guardrails for destructive operations, secrets, credentials, production
systems, write operations, external service calls, and live tests. First verify
that any environment is explicitly test, sandbox, disposable, or
non-production. If that cannot be confirmed, do not run live external changes.

Use static validation, dry runs, local tests, schema validation, rendering, or
linting when live validation is unsafe. Read
`references/safety-and-live-validation.md` before running validation with side
effects.

## Canonical Skill Structure

Read `references/canonical-skill-structure.md` when checking structure,
metadata, naming, section placement, optional resources, or stateful-workflow
profile requirements. Keep the core distinction loaded here:

- OpenAI portable minimum: a skill folder with `SKILL.md` containing front
  matter `name` and `description`.
- OpenAI optional resources: `agents/openai.yaml`, `references/`, `scripts/`,
  and `assets/` when they serve the skill.
- This repository's source-owned standard: every repo-owned skill keeps
  `agents/openai.yaml`; every authorized writable target that completes
  alignment keeps canonical trigger evals; `assets/`, `references/`, and
  `scripts/` are added only when useful.

## Invocation Policy Selection

When creating or hardening OpenAI metadata, derive
`policy.allow_implicit_invocation` from the skill requirements and `SKILL.md`
contract:

- Use `true` for ordinary reusable skills that Codex may safely select when the
  prompt matches their `description`.
- Use `false` when the skill must be explicitly invoked by the user or
  coordinator, such as Git commit/push/PR/merge flows, release/publish flows,
  ordinary auth or local setup, high-risk security mutation, container attachment,
  external MCP installation, workflow test harnesses, or any `sdlc-*` Agentic
  SDLC phase.
- A narrowly scoped setup skill may use `true` only when implicit selection is
  read-only diagnosis/planning and a machine-checkable `## Invocation Policy`
  requires a displayed plan plus explicit current-turn confirmation before
  every IAM, credential, profile, hook, or equivalent mutation.
- For Agentic SDLC phase skills, keep the description prefix
  `Use only as part of the Agentic SDLC workflow;` and set
  `allow_implicit_invocation: false`. The explicit external
  `sdlc-workflow-test` verifier keeps an outside-workflow description and also
  sets `allow_implicit_invocation: false`.
- If a skill's front matter, Non-Goals, Guardrails, or workflow says it should
  run only after an explicit request, encode that requirement in
  `agents/openai.yaml` instead of relying on prose alone.

Use `assets/openai-agent-metadata.yaml.template` as the starting point when a
target skill is missing `agents/openai.yaml`.

For stateful workflow skills, use the template in
`assets/stateful-workflow-skill-template.md`. This profile is opt-in and should
not be forced onto simple instruction-only skills.

## Alignment Workflow

1. Detect target scope: single skill, multiple named skills, parent folder, or
   GitHub source.
2. Classify each target as authorized writable or report-only. Never mutate a
   remote, restricted, or report-only target or describe it as fully aligned.
3. Inspect nearby repository conventions and each target's `SKILL.md` and
   supporting folders. Before editing a dirty writable target, capture its
   current working bytes in a task-owned temporary baseline rather than using
   `HEAD` implicitly.
4. Inventory each target's public actions, positional arguments, and flags.
   Add or repair the standard `## Help` section so its report-only response
   concisely describes every public interface item, and add or repair the
   standard `## Learning Loop` section when missing or unsafe.
5. Identify products, CLIs, APIs, clouds, frameworks, package managers, and
   external services the skill references.
6. Verify vendor-specific behavior against current official documentation.
7. Apply the semantic authoring rubric: tighten the description, use
   directive-first core instructions, remove behavior-neutral prose, calibrate
   workflow freedom, and move deep provider, domain, or workflow variants into
   focused references. For an over-budget or overloaded target, follow the
   conditional progressive-disclosure refactor, including block
   classification, preservation evidence, size evidence, and a split or
   justified-exception decision.
8. For each writable target, create or migrate one canonical
   `evals/trigger-prompts.csv`, select proportionate quality evals or
   deterministic tests, and run every applicable changed-scope eval. Keep the
   CSV target-owned: do not follow symlinked path components or expose raw IDs
   or prompts in validation output. For a report-only target, record the
   missing or existing coverage without writing.
9. Apply focused, evidence-backed improvements across `SKILL.md`, references,
   assets, scripts, metadata, README entries, and changelog entries when those
   surfaces exist and are in scope.
10. Run the mandatory `code-review` and `apply-security` lanes against the
   target skill scope. Resolve safe blocking findings when edits are allowed;
   otherwise report them as blockers, explicit deferrals, or owner-review needs.
11. Capture newly learned durable knowledge, including reusable review-lane
   findings, back into the target skill's local source materials. Prefer the
   narrowest appropriate surface: `SKILL.md` for runtime rules, `references/`
   for detailed guidance, `assets/` for reusable templates, `scripts/` for
   deterministic checks, and README or changelog entries for human-facing or
   release-note updates.
12. Re-run strict static validation for every writable aligned target, then
    report what changed, what passed, and every skipped or unavailable evidence
    lane.

## Live Validation Workflow

Use the safe validation hierarchy:

1. Static checks.
2. Local lint, schema, or render checks.
3. Unit tests.
4. Dry runs.
5. Disposable or sandbox integration tests.
6. Live external tests only after test-environment confirmation.

Use `python3 scripts/validate-skill-structure.py --require-evals <target>` for
authorized writable targets that complete alignment. Add
`--profile stateful-workflow` independently when the optional state-machine
contract applies. Use basic validation without `--require-evals` for legacy
catalog checks; it permits missing evals but still rejects a malformed canonical
CSV that exists. The validator rejects symlinked CSV paths before reading and
reports duplicate rows without echoing raw IDs or prompt content. If script
execution is not permitted, mirror the static checks manually and report that
the validator was skipped.

When changing the validator itself, also run
`python3 scripts/test-validate-skill-structure.py`; it uses temporary local
fixtures plus a read-only pass over the real source catalog and performs no
network or installed-runtime changes.

Report evidence with only these states:

- `STATIC_PASS`: definitions and deterministic static checks passed.
- `RUNTIME_PASS`: fresh-surface trigger behavior was observed.
- `QUALITY_PASS`: output assertions passed against a prior-version or no-skill
  baseline.
- `NOT_RUN`, `UNAVAILABLE`, or `FAIL`: the lane lacks passing evidence.

Do not call a trigger CSV a runtime eval run. Require a fresh trigger check
after invocation changes when a runnable surface exists, and a quality
comparison after material behavior changes when a clean runner and baseline
exist. An executed applicable `FAIL` blocks completion.

## Output Contract

Return:

- Scope inspected.
- Changes made.
- Evidence used and vendor docs checked.
- Validation run.
- Eval files, case counts, baseline, and static/runtime/quality evidence states.
- For an over-budget or overloaded target, block dispositions, preservation
  evidence, before-and-after line and compatible token cost, and the split or
  justified-exception decision. Route any new sibling-skill scaffold to
  `skill-creator`.
- For every working-byte baseline, the exact scoped cleanup result or an
  explicit retention reason, owner-only permissions, cleanup owner, and
  deadline.
- `code-review` lane result.
- `apply-security` lane result.
- Review-lane findings fixed, deferred, skipped, incomplete, or blocking.
- Live tests run or skipped.
- Safety decisions.
- Help-interface coverage for every public action, positional argument, and
  flag, including unresolved interface ambiguity.
- Learning-loop coverage for target skills.
- Source materials updated with reusable learnings, or why updates were skipped.
- Remaining uncertainty.
- Follow-up recommendations.

Use `assets/alignment-report-template.md` for longer reports and
`assets/alignment-plan-template.md` when a plan is needed.

## Stop Conditions

Stop before making live external changes when the environment is not confirmed
as non-production. Stop before destructive commands, credential changes,
publishing, deleting, Terraform apply, Kubernetes mutation, database writes, or
CI/CD writes unless the user explicitly requests them and safety checks pass.

Stop if vendor documentation cannot verify a proposed vendor-specific behavior;
report it as unverified instead.

Stop before writing learned material into reusable skill sources when the
learning is confidential, environment-specific, not evidence-backed, outside
the target skill's scope, or the user requested report-only work. In that case,
report the skipped source update and the reason.

Stop before claiming an authorized writable target is aligned when its
canonical trigger evals are missing or strict validation fails. For remote or
report-only targets, report `EVALS_MISSING` or the observed coverage and keep
the outcome partial without mutation.

Stop before claiming full alignment when mandatory `code-review` or
`apply-security` evidence is missing or incomplete. Report the incomplete lane
and remaining blocker instead.

## Remaining Uncertainty

Runtime skill triggering can be surface- and installation-dependent. If you did
not observe Codex loading the skill in the target surface, report trigger
readiness from metadata inspection only, not as proven runtime activation.
