# Alignment Rubric

Use this rubric to review one skill or a multi-skill folder. Treat it as a
practical checklist, not a reason to rewrite unrelated content.

Source basis:

- [OpenAI Codex best practices](https://developers.openai.com/codex/learn/best-practices)
- [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)
- [Evaluating skill output quality](https://agentskills.io/skill-creation/evaluating-skills)
- [Using scripts in skills](https://agentskills.io/skill-creation/using-scripts)

## Rubric

| Area | Check |
| --- | --- |
| Name and folder consistency | `name` is lowercase hyphenated, valid, and matches the parent folder. |
| Description specificity | Description says what the skill does and when to use it. |
| Trigger quality | Description includes realistic user intent terms and avoids over-broad claims. |
| Scope and non-goals | Skill has a clear job and boundaries from adjacent skills. |
| Progressive disclosure | `SKILL.md` stays focused; long material moves to `references/` or `assets/`. |
| Official vendor evidence | Product-specific commands and claims are checked against official docs. |
| Safety and guardrails | Destructive, credential, publishing, production, and external-service risks are guarded. |
| Environment assumptions | Required tools, access, credentials, and test-environment assumptions are explicit. |
| Live validation policy | Live tests require confirmed non-production context and report skipped work. |
| Script quality | Scripts are self-contained where possible, documented with `--help`, and fail safely. |
| Reference quality | Reference files are focused, current, and loaded only when needed. |
| Asset/template quality | Assets are reusable, generic, and free of secrets or environment-specific values. |
| Output contract | Final answer shape is explicit and matches the task. |
| Testability | Static checks, linting, dry runs, unit tests, or eval prompts are available where useful. |
| Security posture | No secrets, private endpoints, customer data, or unsafe defaults are introduced. |
| Maintainability | Instructions are concise, non-duplicative, and easy to update. |
| Repository conventions | Skill follows local folder, README, metadata, lint, and changelog conventions. |

## Severity Guidance

- Critical: secrets, production mutation risk, destructive commands without
  confirmation, false vendor claims, invalid front matter, or missing
  `SKILL.md`.
- High: wrong trigger scope, broken script, unsafe live-validation guidance, or
  instructions that can write to external services without guardrails.
- Medium: stale examples, missing output contract, missing validation notes, or
  weak progressive disclosure.
- Low: wording polish, minor metadata drift, or optional template gaps.

## Review Method

1. Identify target scope and local conventions.
2. Validate structure before reading for style.
3. Compare `description` against likely should-trigger and should-not-trigger
   prompts.
4. Search the skill for vendor names, CLI commands, APIs, cloud services,
   package managers, auth flows, publishing steps, Kubernetes, Terraform, Helm,
   GitHub Actions, databases, and CI/CD behavior.
5. Verify vendor-specific details against official docs.
6. Apply only evidence-backed changes.
7. Run safe validation and record remaining uncertainty.
