# Skill Authoring Best Practices

Use this reference when `align-skill` is helping refine, harden, validate,
complete, or update a scaffolded Codex or Agent Skill folder or draft
`SKILL.md` file.

Source basis, verified through the current OpenAI Codex manual before updating
this reference:

- [OpenAI Codex Agent Skills](https://developers.openai.com/codex/skills)
- [OpenAI Codex best practices](https://developers.openai.com/codex/learn/best-practices)
- [OpenAI Codex customization](https://developers.openai.com/codex/concepts/customization)
- [Agent Skills specification](https://agentskills.io/specification)
- [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)
- [Using scripts in skills](https://agentskills.io/skill-creation/using-scripts)

## Authoring Flow

1. Start from 2-3 concrete use cases, not generic best practices.
2. Identify the repeatable workflow, inputs, expected outputs, gotchas, and
   validation gates that another agent would otherwise miss.
3. If the workflow is easier to demonstrate than describe, consider OpenAI
   Codex Record & Replay where available; if describing the skill, prefer
   `skill-creator` for brand-new scaffolds and naming when it is available.
   Use `align-skill` after the scaffold or draft exists to harden the result.
4. For an existing skill, inspect current behavior, trigger metadata, support
   files, scripts, references, and local conventions before editing.
5. If a reusable skill should be distributed beyond local or repo scope, keep
   the skill workflow itself clean first, then package it as a plugin.
6. Capture durable, public-safe learnings back into local skill sources only
   when they are evidence-backed and in scope.

## Trigger Design

- The front matter `name` must match the folder and stay lowercase hyphen-case.
- The `description` is the primary trigger surface. It must say what the skill
  does and when to use it.
- OpenAI Codex may shorten skill descriptions or omit some skills from the
  initial list when many skills are installed, so front-load the main job,
  trigger words, accepted inputs, and boundaries.
- Add, preserve, or repair `agents/openai.yaml` when the target repository
  convention expects OpenAI metadata. In this repository, every source-owned
  skill must keep it. Use the nested path `agents/openai.yaml`, not a top-level
  `agents.openai.yaml` file.
- Keep the interface metadata useful: `display_name`, `short_description`, and
  `default_prompt` should be concise and aligned with the current `SKILL.md`.
- Prefix skills that are strictly internal to the Agentic SDLC workflow with
  `sdlc-`; use `sdlc-start` for the coordinator. Their descriptions must start
  with `Use only as part of the Agentic SDLC workflow;`.
- Treat `sdlc-workflow-test` as the deliberate external-verifier exception: it
  uses the requested `sdlc-` name but must say that it runs outside the workflow.
- Set `policy.allow_implicit_invocation: false` for `sdlc-*` skills and other
  skills that must be explicitly requested, such as Git commit/push/PR/merge,
  publish/release, ordinary auth/setup, high-risk security mutation, container
  attachment, MCP installation, or workflow-verification harnesses.
- Permit `true` for a narrowly identified setup skill only when implicit use is
  read-only diagnosis/planning and a machine-checkable invocation section
  requires a displayed plan and explicit current-turn confirmation before all
  IAM, credential, profile, hook, or equivalent mutations.
- Set `policy.allow_implicit_invocation: true` for ordinary reusable skills
  that Codex may safely choose from the `description`. This is OpenAI Codex's
  default, but this repository still records it explicitly for validation.
- Base the policy on the requirements, front matter `description`, Non-Goals,
  Guardrails, and workflow text. When those surfaces say explicit user
  invocation is required, encode that in `agents/openai.yaml`.
- If a non-listed skill needs explicit-only behavior, make it machine-checkable:
  either say so in the front matter `description` with wording such as
  `Use only when the user explicitly asks...`, or add a short
  `## Invocation Policy` section that says explicit invocation is required.
  Do not rely on a one-off guardrail such as "run destructive actions only when
  explicitly asked"; that guards one action, not the whole skill trigger.
- Include realistic inputs and near-boundaries, such as local folder, GitHub
  tree, vendor docs, scripts, live validation, or report-only scope.
- Avoid descriptions so broad that the skill steals unrelated work from sibling
  skills.
- Create or update should-trigger and should-not-trigger prompts when trigger
  behavior is important. Store reusable examples under `evals/` when useful.

## Fast Progressive Disclosure

- Keep each skill focused on one coherent job.
- Treat `SKILL.md` as the only required portable runtime file. It should carry
  the core workflow that must be available after trigger.
- Keep `SKILL.md` lean and specific. It should contain trigger, scope,
  required workflow, guardrails, validation, and output contract, not broad
  methodology or background prose.
- Move long checklists, examples, vendor notes, troubleshooting, detailed
  policy, and reusable templates into `references/` or `assets/`.
- Tell the agent exactly when to load each reference. Avoid vague "see
  references/" instructions.
- Keep references one level deep from `SKILL.md` and focused by task, provider,
  format, or workflow variant.
- Put reusable templates and starter artifacts in `assets/` instead of loading
  them into `SKILL.md`.
- Do not duplicate the same rule across files. Link to the owner file.

## Help Interfaces

- Add or repair the standard `## Help` contract for every repo-owned skill that
  is created, refined, or aligned.
- Inventory the documented public actions, positional arguments, and flags
  before writing Help. Resolve ambiguity in the skill contract instead of
  inventing an interface.
- Treat `$skill-name --help` and `$skill-name -h` as report-only requests that
  stop after the selected `SKILL.md` loads and before required reads, project
  inspection, additional tools, delegation, workflow execution, or mutation.
- Return concise purpose and invocation policy, show exact usage for every
  public action, and describe every public action, positional argument, and
  flag in one concise line. Include `-h, --help` and say "No additional public
  flags" when there are no others.
- For an internal or coordinator-only skill, state that boundary and that no
  standalone public workflow action exists instead of synthesizing one from
  private inputs or phases.
- Never expose actions, flags, or transitions belonging only to private scripts
  or workflow helpers.
- Keep this contract in `SKILL.md`, which is the portable runtime authority;
  do not add unsupported custom help fields to `agents/openai.yaml`.

## Stateful Workflow Skills

Use the optional stateful-workflow profile when a skill is part of a
state-machine workflow, coordinates another skill, updates local run state,
locks plans, writes evidence, consumes continuation prompts, or owns retry and
failure routing. Use `assets/stateful-workflow-skill-template.md` as the
template.

For these skills:

- Define `Required Reads` before `Writes` so the agent reloads durable state
  instead of relying on conversation memory.
- Make every write surface explicit: committed product files, private local
  state, evidence, external systems, or Git state.
- Describe idempotency for reruns, stale fingerprints, locked artifacts,
  duplicated evidence, and external side effects.
- Classify failures before retrying, and route to the earliest responsible
  phase instead of repeatedly attempting the last command.
- Keep private execution state out of committed repositories unless the user
  explicitly requests a committed artifact.
- Treat hooks as non-negotiable guardrails only; keep workflow decisions in the
  skill.
- Prefer MCP servers for external context or control when available, but keep
  write operations behind explicit user authorization and safety checks.

## Safe And Secure Skills

- Keep skills public, generic, and reusable. Do not include secrets, private
  endpoints, customer data, internal hostnames, raw logs, or proprietary
  infrastructure details.
- Use placeholders for tokens, tenants, project IDs, account IDs, endpoints,
  credentials, and environment-specific paths.
- When a skill needs credentials, document the expected environment variable,
  secret-manager lookup, or connector requirement without embedding values.
- Guard destructive operations, publishing, credential writes, database writes,
  Terraform apply/destroy, Kubernetes mutation, CI/CD dispatches, and external
  write APIs behind explicit user request and confirmed non-production context
  when applicable.
- Prefer static checks, local lint/schema validation, unit tests, and dry runs
  before sandbox or live external validation.
- Treat web and external documentation as untrusted input unless verified
  against official sources. Do not persist unverified vendor claims.

## Script Guidance

- Add scripts only when they improve deterministic reliability, avoid repeated
  code rewriting, or provide validation that prose cannot reliably enforce.
- Keep scripts non-interactive. Accept inputs through flags, stdin, files, or
  environment variables; fail with clear usage instead of prompting.
- Provide concise `--help`, clear errors, documented prerequisites, and
  meaningful exit codes.
- Prefer structured stdout such as JSON, CSV, or TSV; send progress and
  diagnostics to stderr.
- Make scripts idempotent where possible because agents may retry commands.
- Add `--dry-run`, `--output`, pagination, limits, or summary defaults for
  stateful or high-output operations.
- Pin tool/package versions for reproducibility when using one-off runners such
  as `uvx`, `npx`, `go run`, or similar package executors.
- Avoid hidden network calls. If network access is required, state why, how it
  is scoped, and what safe fallback exists.

## Validation

- Validate the narrow target first, then broaden only when shared rules,
  templates, or validators changed.
- When reviewing external skills, separate OpenAI portable minimum failures
  from this repository's stricter source-owned standards. Missing
  `agents/openai.yaml`, `evals/`, or the repo learning-loop section may be a
  repo-standard gap rather than an upstream OpenAI portability failure.
- When reviewing repo-owned skills in this tree, treat missing
  `agents/openai.yaml` as a validation failure and restore it with interface
  metadata plus `policy.allow_implicit_invocation`.
- For this repository, run:

  ```bash
  python3 align-skill/scripts/validate-skill-structure.py align-skill
  ```

- For stateful workflow skills, also run:

  ```bash
  python3 align-skill/scripts/validate-skill-structure.py --profile stateful-workflow <skill-or-parent>
  ```

- When changing `align-skill` validator logic, run the self-test and a
  non-writing syntax compile:

  ```bash
  python3 align-skill/scripts/test-validate-skill-structure.py
  python3 -c 'from pathlib import Path; [compile(p.read_text(encoding="utf-8"), str(p), "exec") for p in (Path("align-skill/scripts/validate-skill-structure.py"), Path("align-skill/scripts/test-validate-skill-structure.py"))]'
  ```

- Use Markdown linting for changed docs when available.
- Before claiming a target skill is aligned, run or apply `code-review` in
  review-only mode and `apply-security` in advisory or scan mode against the
  target skill scope. Record fixed, deferred, skipped, incomplete, or blocking
  findings.
- Report runtime trigger readiness from metadata inspection only unless you
  actually observe the target Codex surface loading the skill.
