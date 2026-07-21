# Align Skill

`align-skill` reviews and hardens existing or newly scaffolded Codex or Agent
Skill folders. Use it when a skill needs structure, metadata, references,
assets, scripts, trigger behavior, vendor-doc grounding, safety guardrails, fast
validation, or validation evidence aligned.

## What It Does

- Checks `SKILL.md` front matter, scope, trigger quality, and workflow clarity.
- Helps refine draft or scaffolded skills with safe, secure, fast authoring
  practices.
- Separates the OpenAI portable minimum structure from this repository's
  stricter source-owned skill standard.
- Validates repo-required `agents/openai.yaml` metadata plus optional
  `assets/`, `evals/`, `references/`, and `scripts/` surfaces.
- Creates or repairs `agents/openai.yaml` metadata when the repository
  convention requires it, including the correct
  `policy.allow_implicit_invocation` value for the skill contract.
- Adds or repairs the standard `## Learning Loop` rule on target skills.
- Verifies vendor-specific claims against official documentation when needed.
- Adds guardrails for destructive actions, secrets, live systems, and external
  services.
- Runs mandatory `code-review` and `apply-security` lanes for every target
  skill before reporting the target as aligned.
- Applies an optional stateful-workflow profile for coordinator or
  state-machine skills that manage local state, locked plans, evidence,
  retries, or failure routing.
- Captures durable reusable learnings back into the skill's local source
  materials before completion.
- Runs static skill validation before reporting readiness.

## Architecture

```text
Skill folder
  |
  +--> SKILL.md required runtime instructions
  +--> agents/openai.yaml optional upstream metadata, required here
  +--> references/ optional detailed docs
  +--> assets/ optional reusable templates
  +--> evals/ optional repo trigger and quality examples
  `--> scripts/ optional deterministic helpers
        |
        v
align-skill checks structure, safety, docs, and validation
```

## Workflow

1. Detect the target skill scope.
2. Inspect current skill files and nearby repository conventions.
3. Verify relevant product or API claims against official docs.
4. Add or repair the target skill's `## Learning Loop` section.
5. Apply focused updates to metadata, instructions, references, assets, or
   scripts.
6. For stateful workflow skills, use
   `assets/stateful-workflow-skill-template.md` and validate with
   `scripts/validate-skill-structure.py --profile stateful-workflow`.
7. Run `code-review` in review-only mode and `apply-security` in advisory or
   scan mode against the target skill scope.
8. Update local skill source materials with evidence-backed reusable learnings
   discovered during execution, including reusable review-lane findings.
9. Run `scripts/validate-skill-structure.py` when available.
10. Report validation, review-lane results, learning-loop coverage,
    source-material updates, skipped live checks, and remaining uncertainty.

## Core Concepts

- Keep `SKILL.md` concise for progressive disclosure.
- Keep `SKILL.md` limited to trigger, scope, required workflow, guardrails,
  validation, and output contract; move long rubrics, examples, and templates
  into supporting folders.
- Treat `SKILL.md` with front matter `name` and `description` as the OpenAI
  portable minimum.
- Move detailed references and templates into supporting folders.
- Use `skill-creator` for new-skill scaffolding when available, then use
  `align-skill` for authoring hardening and validation.
- Use the exact metadata path `agents/openai.yaml`. In this repository, every
  source-owned skill must keep that file even though OpenAI Codex treats it as
  optional metadata.
- Set `policy.allow_implicit_invocation` to `false` for explicit-only,
  mutating, publishing, ordinary setup, or Agentic SDLC phase skills; use
  `true` for ordinary reusable skills. A narrow setup exception may use `true`
  only when implicit work is read-only and explicit current-turn confirmation
  follows a displayed mutation plan.
- Do not broaden a skill until its trigger becomes hard to reason about.
- Capture durable knowledge in reusable skill sources, not in ad hoc notes or
  final-answer-only summaries.
- Do not claim runtime activation unless the target Codex surface proves it.

## Stateful Workflow Skills

A stateful workflow skill is a skill that must resume, coordinate, or validate
work from durable state or artifacts instead of only reacting to the current
prompt. It usually reads a known state file, locked plan, checkpoint, or
evidence bundle; writes updated state, evidence, or external progress; and
defines how reruns avoid duplicate work.

Use the stateful-workflow profile when a skill coordinates phases, selects the
next skill, owns local run state, writes evidence, handles retries, or routes
failures. Do not use it for simple instruction-only skills that can run from the
prompt and current files alone.

Concise example: `sdlc-start` is stateful because it reads the active SDLC run
state, selects the next `sdlc-*` phase, writes checkpoints, and resumes safely
after retries or compaction. A simple `.gitignore` cleanup skill is not
stateful if it only inspects files, edits `.gitignore`, and reports the result.

## Files

- `SKILL.md`: runtime alignment workflow for skills.
- `agents/openai.yaml`: UI metadata and invocation policy.
- `references/`: canonical structure, authoring, safety, vendor, and trigger
  guidance.
- `assets/`: report, plan, OpenAI metadata, and stateful-workflow skill
  templates.
- `scripts/validate-skill-structure.py`: static skill folder validator,
  including `policy.allow_implicit_invocation` checks.
- `scripts/test-validate-skill-structure.py`: local fixture self-test for the
  validator.
