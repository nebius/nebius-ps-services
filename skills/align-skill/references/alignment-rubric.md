# Alignment Rubric

Use this rubric to review one skill or a multi-skill folder. Treat it as a
practical checklist, not a reason to rewrite unrelated content.

Source basis:

- [OpenAI Codex Agent Skills](https://developers.openai.com/codex/skills)
- [OpenAI Codex best practices](https://developers.openai.com/codex/learn/best-practices)
- [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)
- [Evaluating skill output quality](https://agentskills.io/skill-creation/evaluating-skills)
- [Using scripts in skills](https://agentskills.io/skill-creation/using-scripts)

## Rubric

| Area | Check |
| --- | --- |
| Structure profile | Review distinguishes OpenAI portable minimum requirements from local repo-specific standards. |
| Name and folder consistency | `name` is lowercase hyphenated, valid, and matches the parent folder. |
| SDLC-only naming | Skills used only inside the Agentic SDLC state machine use `sdlc-*` names and start descriptions with `Use only as part of the Agentic SDLC workflow;`. |
| Description specificity | Description says what the skill does and when to use it. |
| Trigger quality | Description includes realistic user intent terms and avoids over-broad claims. |
| Authoring helper fit | For scaffolded skill folders or draft skill content, guidance defers initial scaffolding to `skill-creator` and then hardens trigger, structure, safety, speed, and validation. |
| Scope and non-goals | Skill has a clear job and boundaries from adjacent skills. |
| Progressive disclosure | `SKILL.md` carries only trigger, scope, required workflow, guardrails, validation, and output contract; long material moves to `references/` or `assets/`. |
| Code-review lane | `code-review` reviews the target skill scope for instruction quality, support scripts, validation gaps, maintainability, over-complexity, and bloated `SKILL.md` content. |
| Security-review lane | `apply-security` reviews the target skill scope for secrets, private URLs, unsafe live actions, credential handling, external writes, dangerous scripts, and supply-chain risk. |
| Official vendor evidence | Product-specific commands and claims are checked against official docs. |
| Safety and guardrails | Destructive, credential, publishing, production, and external-service risks are guarded. |
| Environment assumptions | Required tools, access, credentials, and test-environment assumptions are explicit. |
| Live validation policy | Live tests require confirmed non-production context and report skipped work. |
| Script quality | Scripts are self-contained where possible, documented with `--help`, and fail safely. |
| Script speed and ergonomics | Scripts are non-interactive, idempotent where possible, version-pinned when using package runners, and produce bounded structured output. |
| Reference quality | Reference files are focused, current, and loaded only when needed. |
| Asset/template quality | Assets are reusable, generic, and free of secrets or environment-specific values. |
| Output contract | Final answer shape is explicit and matches the task. |
| Testability | Static checks, linting, dry runs, unit tests, or eval prompts are available where useful. |
| Security posture | No secrets, private endpoints, customer data, or unsafe defaults are introduced. |
| Stateful workflow profile | State-machine skills define required reads, writes, idempotency, failure handling, must-not rules, and completion criteria. |
| Private state boundary | Workflow execution state, locked plans, evidence, screenshots, transcripts, and steering files are kept out of committed project files unless explicitly intended. |
| Hook boundary | Hooks enforce invariants only and do not become the workflow orchestrator. |
| Help interface | After the selected `SKILL.md` loads, `$skill-name --help` and `$skill-name -h` stop before additional tools or mutation and report concise purpose, invocation policy, public usage/actions, and documented options without exposing private helper flags. |
| Learning loop coverage | Each target `SKILL.md` has a `## Learning Loop` section containing the validator-required public-safe source-learning text. |
| Learning capture | Durable, reusable knowledge discovered during execution is captured in the local skill source materials when evidence-backed and in scope. |
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
4. For scaffolded skill folders, draft skill content, or update tasks, read
   `references/skill-authoring-best-practices.md` and check the target against
   safe, secure, and fast authoring guidance.
5. For state-machine or coordinator skills, decide whether the optional
   stateful-workflow profile applies. If it applies, check the profile sections
   manually or run `validate-skill-structure.py --profile stateful-workflow`.
6. Confirm the target `SKILL.md` has standard `## Help` and `## Learning Loop`
   sections. Repair help that can execute or expose private flags, and repair
   learning-loop variants that omit validator-required snippets.
7. Search the skill for vendor names, CLI commands, APIs, cloud services,
   package managers, auth flows, publishing steps, Kubernetes, Terraform, Helm,
   GitHub Actions, databases, and CI/CD behavior.
8. Verify vendor-specific details against official docs.
9. Apply only evidence-backed changes.
10. Apply the mandatory `code-review` and `apply-security` lanes to every target
   skill in scope. Keep both lanes scoped to target skill files and directly
   referenced resources, and keep them report-only when the user requested
   report-only work.
11. Capture newly learned durable patterns, decisions, best practices, or
   relevant findings, including reusable review-lane findings, in the target
   skill's local sources when they are reusable, public-safe, and in scope.
12. Run safe validation and record remaining uncertainty. Do not claim full
    alignment when either mandatory review lane is incomplete or has unresolved
    blocking findings.

Do not capture raw logs, secrets, customer data, private URLs, transient local
state, or one-off environment details. If a useful learning is not safe or
appropriate to persist, report that the source update was skipped and why.
