# Align Skill

`align-skill` reviews and improves Codex or Agent Skill folders. Use it when a
skill needs structure, metadata, references, assets, scripts, trigger behavior,
vendor-doc grounding, safety guardrails, or validation evidence aligned.

## What It Does

- Checks `SKILL.md` front matter, scope, trigger quality, and workflow clarity.
- Validates optional `agents/`, `assets/`, `references/`, and `scripts/`
  surfaces.
- Verifies vendor-specific claims against official documentation when needed.
- Adds guardrails for destructive actions, secrets, live systems, and external
  services.
- Runs static skill validation before reporting readiness.

## Architecture

```text
Skill folder
  |
  +--> SKILL.md runtime instructions
  +--> agents/openai.yaml metadata
  +--> references/ detailed docs
  +--> assets/ reusable templates
  `--> scripts/ deterministic helpers
        |
        v
align-skill checks structure, safety, docs, and validation
```

## Workflow

1. Detect the target skill scope.
2. Inspect current skill files and nearby repository conventions.
3. Verify relevant product or API claims against official docs.
4. Apply focused updates to metadata, instructions, references, assets, or
   scripts.
5. Run `scripts/validate-skill-structure.py` when available.
6. Report validation, skipped live checks, and remaining uncertainty.

## Core Concepts

- Keep `SKILL.md` concise for progressive disclosure.
- Move detailed references and templates into supporting folders.
- Do not broaden a skill until its trigger becomes hard to reason about.
- Do not claim runtime activation unless the target Codex surface proves it.

## Files

- `SKILL.md`: runtime alignment workflow for skills.
- `agents/openai.yaml`: UI metadata.
- `references/`: canonical structure, safety, vendor, and trigger guidance.
- `assets/`: report and plan templates.
- `scripts/validate-skill-structure.py`: static skill folder validator.
