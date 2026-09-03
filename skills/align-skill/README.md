# Align Skill

`align-skill` reviews and hardens existing or newly scaffolded Codex or Agent
Skill folders. Use it when a skill needs structure, metadata, references,
assets, scripts, trigger behavior, vendor-doc grounding, safety guardrails, fast
validation, or validation evidence aligned.

## What It Does

- Checks `SKILL.md` front matter, scope, trigger quality, and workflow clarity.
- Keeps core instructions directive-first and within a soft 500-line review
  budget through progressive disclosure, focused references, and calibrated
  workflow freedom.
- For over-budget or overloaded skills, classifies blocks by runtime ownership,
  preserves safety and decision-changing rationale, records comparable context
  cost, and requires either a semantic refactor or a justified exception.
- Helps refine draft or scaffolded skills with safe, secure, fast authoring
  practices.
- Separates the OpenAI portable minimum structure from this repository's
  stricter source-owned skill standard.
- Validates repo-required `agents/openai.yaml` metadata, canonical per-target
  trigger evals, and optional `assets/`, `references/`, and `scripts/` surfaces.
- Creates or repairs `agents/openai.yaml` metadata when the repository
  convention requires it, including the correct
  `policy.allow_implicit_invocation` value for the skill contract.
- Adds or repairs the standard side-effect-free `## Help` contract so concise
  help describes every public action, positional argument, and flag, plus the
  standard `## Learning Loop` rule on target skills.
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
- Separates static definition checks, fresh-runtime trigger observations, and
  output-quality comparisons so unavailable evidence is never implied.

## Architecture

```text
Skill folder
  |
  +--> SKILL.md required runtime instructions
  +--> agents/openai.yaml optional upstream metadata, required here
  +--> references/ optional detailed docs
  +--> assets/ optional reusable templates
  +--> evals/ canonical trigger cases plus proportionate quality cases
  `--> scripts/ optional deterministic helpers
        |
        v
align-skill checks structure, safety, docs, and validation
```

## Workflow

1. Detect the target skill scope.
2. Inspect current skill files and nearby repository conventions.
3. Verify relevant product or API claims against official docs.
4. Inventory the target's documented public actions, positional arguments, and
   flags. Add or repair `## Help` so each public item has exact usage and a
   concise description, then add or repair `## Learning Loop`.
5. Apply focused updates to metadata, instructions, references, assets, or
   scripts; move deep provider or domain variants out of `SKILL.md`. For an
   over-budget or overloaded target, use the conditional progressive-disclosure
   refactor instead of summarizing sections mechanically.
6. Create or migrate each writable target's canonical
   `evals/trigger-prompts.csv`, then select proportionate deterministic or
   output-quality evidence.
7. For stateful workflow skills, use
   `assets/stateful-workflow-skill-template.md` and validate with
   `scripts/validate-skill-structure.py --profile stateful-workflow
   --require-evals <target>`.
8. Run `code-review` in review-only mode and `apply-security` in advisory or
   scan mode against the target skill scope.
9. Update local skill source materials with evidence-backed reusable learnings
   discovered during execution, including reusable review-lane findings.
10. Run `scripts/validate-skill-structure.py --require-evals <target>` for each
    writable aligned target. Keep default validation for legacy catalog checks.
11. Report static, runtime, and quality evidence separately with review-lane
    results, source-material updates, skipped checks, and uncertainty.

## Core Concepts

- Keep `SKILL.md` concise for progressive disclosure.
- Treat more than 500 lines as a review warning, not an automatic failure.
- Measure long-skill lines before and after; compare token cost only with a
  compatible method, and report it as unavailable rather than adding a
  tokenizer dependency or approximate threshold.
- Split independently triggered jobs when their outcomes are distinct, not to
  satisfy a size target. Keep justified always-needed or safety-critical core
  above 500 lines when non-regression evidence supports it.
- Remove behavior-neutral prose but preserve rationale that changes decisions,
  safety, routing, validation, or output.
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
- Treat `$skill-name --help` and `$skill-name -h` as report-only: show purpose
  and invocation policy, exact usage for every public action, and one concise
  description for every public action, positional argument, and flag. Include
  `-h, --help`, say when there are no additional public flags, then stop after
  the selected `SKILL.md` loads without additional tools or mutation.
- For internal or coordinator-only skills, report that boundary and no
  standalone public workflow action instead of exposing private phases.
- Capture durable knowledge in reusable skill sources, not in ad hoc notes or
  final-answer-only summaries.
- Do not claim runtime activation unless the target Codex surface proves it.
- Do not mutate report-only or remote targets; report missing canonical evals
  and keep the result partial.
- Treat `evals/trigger-prompts.csv` as target-owned input: reject symlinked
  file or directory components, and report duplicate rows without echoing IDs
  or prompt text into logs.

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
- `references/evaluation-guide.md`: trigger schema, quality-oracle selection,
  baseline handling, evidence states, and efficient execution order.
- `references/progressive-disclosure-refactor.md`: conditional long-skill block
  classification, preservation, cost evidence, split decisions, and justified
  exceptions.
- `evals/`: `align-skill` trigger cases and output-quality assertions.
- `assets/`: report, plan, OpenAI metadata, and stateful-workflow skill
  templates.
- `scripts/validate-skill-structure.py`: static skill folder validator,
  including help, invocation policy, canonical eval, and line-budget checks.
- `scripts/test-validate-skill-structure.py`: local fixture self-test plus a
  read-only real-source catalog regression for the validator.
- `scripts/validator_eval_contract_cases.py`: focused strict-eval and line-budget
  fixtures kept separate from the main structural test module.
