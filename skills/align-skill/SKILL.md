---
name: align-skill
description: "Use for Codex or Agent Skill folder alignment: SKILL.md front matter/YAML metadata, name/description trigger quality, implicit skill detection, duplicate or stale descriptions, canonical structure, official vendor-doc checks, safety guardrails, and validation."
---

# Align Skill

## Purpose

Use this skill to inspect, align, harden, validate, and improve one or more
Codex or Agent Skill folders. The target is skill quality: `SKILL.md`,
supporting `references/`, `assets/`, `scripts/`, optional `agents/` metadata,
triggering behavior, safety rules, and validation evidence.

This skill is separate from `align`, which is for end-to-end project/codebase
alignment.

## Use This Skill For

- Aligning a named skill, local skill folder, multi-skill parent folder, GitHub
  repository URL, or GitHub tree URL.
- Reviewing or fixing `SKILL.md` front matter, description trigger quality,
  scope, workflow clarity, output contract, and progressive disclosure.
- Standardizing skill folders against a canonical skill structure.
- Checking skill guidance, commands, scripts, examples, and vendor-specific
  claims against current official documentation.
- Adding or hardening safety guardrails before validation or live tests.

## Inputs Accepted

- Skill names available in the current workspace or installed skill paths.
- Local paths to one skill folder or a parent folder containing multiple
  skills.
- GitHub repository URLs or GitHub tree URLs that contain one or more skills.
- User-provided constraints such as "report only", "do not run live tests", or
  "only update `SKILL.md`".

## Non-Goals

- Do not use this for general codebase alignment; use `align` for that.
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
Align these skills against the canonical structure and official vendor docs.
Review all skills under this folder and produce an alignment report.
Validate this GitHub skills repo and propose safe changes.
Fix the `SKILL.md` for this skill so it follows Codex Skill best practices.
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
- Keep skills scoped to their actual job and keep `SKILL.md` short enough for
  progressive disclosure.
- Move large checklists, templates, policies, and long examples into
  `references/` or `assets/`.
- Scripts should be self-contained where possible, have helpful errors, avoid
  network calls unless explicitly needed, and fail safely.

Read `references/canonical-skill-structure.md` and
`references/alignment-rubric.md` when structure or quality criteria are in
scope.

## Evidence and Vendor Verification

For every product, framework, SDK, CLI, API, or cloud service used by the target
skill, check current official vendor documentation before changing related
guidance, commands, examples, or code. Prefer official docs over blogs,
tutorials, generated examples, Stack Overflow, or memory.

If official documentation does not verify a vendor-specific behavior, mark it
as unverified instead of presenting it as fact. Read
`references/vendor-research-policy.md` for the full policy.

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
|-- references/
`-- scripts/
```

Only add optional folders when they serve the skill. Follow existing repository
conventions when they are clearer or stricter than the generic structure.

## Alignment Workflow

1. Detect target scope: single skill, multiple named skills, parent folder, or
   GitHub source.
2. Inspect nearby repository conventions before editing.
3. Read the target `SKILL.md` files and supporting folders.
4. Identify products, CLIs, APIs, clouds, frameworks, package managers, and
   external services the skill references.
5. Verify vendor-specific behavior against current official documentation.
6. Apply focused, evidence-backed improvements across `SKILL.md`, references,
   assets, scripts, metadata, README entries, and changelog entries when those
   surfaces exist and are in scope.
7. Validate and report what was verified, skipped, or remains uncertain.

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
current user and repository policy. If script execution is not permitted,
mirror the same static checks manually and report that the validator was
skipped.

## Output Contract

Return:

- Scope inspected.
- Changes made.
- Evidence used and vendor docs checked.
- Validation run.
- Live tests run or skipped.
- Safety decisions.
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

## Remaining Uncertainty

Runtime skill triggering can be surface- and installation-dependent. If you did
not observe Codex loading the skill in the target surface, report trigger
readiness from metadata inspection only, not as proven runtime activation.
