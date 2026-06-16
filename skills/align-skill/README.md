# Align Skill

`align-skill` reviews and hardens existing or newly scaffolded Codex or Agent
Skill folders. Use it when a skill needs structure, metadata, references,
assets, scripts, trigger behavior, vendor-doc grounding, safety guardrails, fast
validation, or validation evidence aligned.

## What It Does

- Checks `SKILL.md` front matter, scope, trigger quality, and workflow clarity.
- Helps refine draft or scaffolded skills with safe, secure, fast authoring
  practices.
- Validates optional `agents/`, `assets/`, `evals/`, `references/`, and
  `scripts/` surfaces.
- Adds or repairs the standard `## Learning Loop` rule on target skills.
- Verifies vendor-specific claims against official documentation when needed.
- Adds guardrails for destructive actions, secrets, live systems, and external
  services.
- Captures durable reusable learnings back into the skill's local source
  materials before completion.
- Runs static skill validation before reporting readiness.

## Architecture

```text
Skill folder
  |
  +--> SKILL.md runtime instructions
  +--> agents/openai.yaml metadata
  +--> references/ detailed docs
  +--> assets/ reusable templates
  +--> evals/ trigger and quality examples
  `--> scripts/ deterministic helpers
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
6. Update local skill source materials with evidence-backed reusable learnings
   discovered during execution.
7. Run `scripts/validate-skill-structure.py` when available.
8. Report validation, learning-loop coverage, source-material updates, skipped
   live checks, and remaining uncertainty.

## Core Concepts

- Keep `SKILL.md` concise for progressive disclosure.
- Move detailed references and templates into supporting folders.
- Use `skill-creator` for new-skill scaffolding when available, then use
  `align-skill` for authoring hardening and validation.
- Do not broaden a skill until its trigger becomes hard to reason about.
- Capture durable knowledge in reusable skill sources, not in ad hoc notes or
  final-answer-only summaries.
- Do not claim runtime activation unless the target Codex surface proves it.

## Files

- `SKILL.md`: runtime alignment workflow for skills.
- `agents/openai.yaml`: UI metadata.
- `references/`: canonical structure, authoring, safety, vendor, and trigger
  guidance.
- `assets/`: report and plan templates.
- `scripts/validate-skill-structure.py`: static skill folder validator.
- `scripts/test-validate-skill-structure.py`: local fixture self-test for the
  validator.
