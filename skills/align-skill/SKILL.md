---
name: align-skill
description: "Use for reviewing, hardening, validating, aligning, and improving existing or newly scaffolded Codex or Agent Skill folders. Use as a skill-writing helper after skill-creator or a manual scaffold/draft exists: refine SKILL.md front matter, description trigger quality, progressive disclosure, canonical structure, safety/security guardrails, fast validation, scripts/references/assets/evals, official vendor-doc checks, and reusable learning capture. Do not replace skill-creator for initial new-skill scaffolding."
---

# Align Skill

## Purpose

Use this skill to inspect, review, align, harden, validate, and improve one or
more Codex or Agent Skill folders. It can help refine skill-writing drafts after
a target skill folder, scaffold, or `SKILL.md` draft exists. The target is skill
quality: `SKILL.md`, supporting `references/`, `assets/`, `scripts/`, optional
`agents/` metadata, optional `evals/` trigger examples, triggering behavior,
safety/security rules, fast validation, and validation evidence.

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
- Reviewing trigger/evaluation prompts stored under `evals/`.
- Checking skill guidance, commands, scripts, examples, and vendor-specific
  claims against current official documentation.
- Adding or hardening safety guardrails before validation or live tests.
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
- Do not run live external changes unless a non-production test environment is
  confirmed.

## Triggering This Skill

Official OpenAI documentation confirms that Codex Skills are available in the
Codex CLI, IDE extension, and Codex app. It also confirms progressive
disclosure: Codex starts with each skill's `name`, `description`, and file path,
then loads the full `SKILL.md` only when it decides the skill is relevant.

Confirmed explicit invocation in CLI/IDE: run `/skills` or type `$` to mention a
skill. Implicit invocation depends on the front matter `description`.

For reliable activation in CLI, IDE extension, or the Codex app, mention
`align-skill` and the target path, skill name, folder, GitHub repository URL, or
GitHub tree URL. Do not claim support for manual `@skill` syntax or any other
explicit invocation syntax unless current official OpenAI documentation
confirms it.

Practical prompts:

```text
Use align-skill to review and align `skills/foo`.
Use align-skill as a helper after skill-creator scaffolded a release-triage skill.
Align these skills against the canonical structure and official vendor docs.
Review all skills under this folder and produce an alignment report.
Validate this GitHub skills repo and propose safe changes.
Fix the `SKILL.md` for this skill so it follows Codex Skill best practices.
Help me harden this draft `SKILL.md` into a safe, secure, fast Codex skill.
Add the standard learning-loop rule to every skill under `skills/`.
Check whether this skill has safe guardrails before live validation.
Standardize this multi-skill folder and add missing references, assets, or scripts.
Review this skill's vendor-specific commands against official documentation.
Align `skills/foo` and `skills/bar`, but do not run live tests unless the environment is confirmed as non-production.
```

VS Code-compatible IDE example: open the repository in VS Code, Cursor, or
Windsurf, then ask: "Use align-skill to align `skills/foo`."

Codex app or desktop local example: open the local project that contains the
skills, choose the local workflow, then ask: "Use align-skill to review all
skills under `skills/`."

For expanded CLI, VS Code-compatible IDE, and Codex app guidance, read
`references/triggering-guide.md`.

## Alignment Principles

- Evidence-based changes only: use repo evidence, official vendor
  documentation, or explicit user requirements.
- Continuous source learning: before completion, update the locally cloned
  source materials associated with the target skill with durable, reusable
  knowledge discovered during the run. Capture patterns, decisions, best
  practices, and relevant findings only when they are evidence-backed,
  public-safe, and in scope.
- Keep skills scoped to their actual job and keep `SKILL.md` short enough for
  progressive disclosure.
- Move large checklists, templates, policies, and long examples into
  `references/` or `assets/`.
- Scripts should be self-contained where possible, have helpful errors, avoid
  network calls unless explicitly needed, and fail safely.
- Do not persist secrets, private URLs, customer data, raw logs, or one-off
  environment details into reusable skill sources.

Read `references/canonical-skill-structure.md` and
`references/alignment-rubric.md` when structure or quality criteria are in
scope.

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
3. Start from concrete use cases and should-trigger/should-not-trigger prompts.
4. Keep the skill focused on one repeatable job and front-load the `description`
   with user intent, accepted inputs, and boundaries from adjacent skills.
5. Apply safe, secure, and fast skill guidance from
   `references/skill-authoring-best-practices.md`.
6. For stateful workflow skills, add explicit `Required Reads`, `Writes`,
   `Idempotency`, `Failure Handling`, `Must Not`, and `Completion Criteria`
   sections. Keep private execution state out of committed project files and
   keep hooks as invariant guardrails rather than workflow orchestrators.
7. Validate locally with the narrowest relevant checks, then broaden only when
   the contract or shared validator changed.

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

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

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

Default target structure:

```text
skill-name/
|-- SKILL.md
|-- agents/openai.yaml
|-- assets/
|-- evals/
|-- references/
`-- scripts/
```

Only add optional folders when they serve the skill. Follow existing repository
conventions when they are clearer or stricter than the generic structure. In
this repository, `evals/` is an optional surface for reusable trigger or quality
evaluation prompts.

For stateful workflow skills, use the template in
`assets/stateful-workflow-skill-template.md`. This profile is opt-in and should
not be forced onto simple instruction-only skills.

## Alignment Workflow

1. Detect target scope: single skill, multiple named skills, parent folder, or
   GitHub source.
2. Inspect nearby repository conventions before editing.
3. Read the target `SKILL.md` files and supporting folders.
4. Add or repair the standard `## Learning Loop` section in target `SKILL.md`
   files when missing or unsafe.
5. Identify products, CLIs, APIs, clouds, frameworks, package managers, and
   external services the skill references.
6. Verify vendor-specific behavior against current official documentation.
7. Apply focused, evidence-backed improvements across `SKILL.md`, references,
   assets, scripts, metadata, README entries, and changelog entries when those
   surfaces exist and are in scope.
8. Capture newly learned durable knowledge back into the target skill's local
   source materials. Prefer the narrowest appropriate surface: `SKILL.md` for
   runtime rules, `references/` for detailed guidance, `assets/` for reusable
   templates, `scripts/` for deterministic checks, and README or changelog
   entries for human-facing or release-note updates.
9. Re-run relevant static validation after source-material updates, then report
   what was changed, captured, skipped, or remains uncertain.

## Live Validation Workflow

Use the safe validation hierarchy:

1. Static checks.
2. Local lint, schema, or render checks.
3. Unit tests.
4. Dry runs.
5. Disposable or sandbox integration tests.
6. Live external tests only after test-environment confirmation.

Use `python3 scripts/validate-skill-structure.py <target>` when this skill's
validator is available, relevant, and script execution is permitted by the
current user and repository policy. For stateful workflow skills, add
`--profile stateful-workflow` to check the optional state-machine section
contract. If script execution is not permitted, mirror the same static checks
manually and report that the validator was skipped.

When changing the validator itself, also run
`python3 scripts/test-validate-skill-structure.py`; it uses temporary local
fixtures and performs no network or installed-runtime changes.

## Output Contract

Return:

- Scope inspected.
- Changes made.
- Evidence used and vendor docs checked.
- Validation run.
- Live tests run or skipped.
- Safety decisions.
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

## Remaining Uncertainty

Runtime skill triggering can be surface- and installation-dependent. If you did
not observe Codex loading the skill in the target surface, report trigger
readiness from metadata inspection only, not as proven runtime activation.
