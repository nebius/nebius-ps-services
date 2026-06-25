# Canonical Skill Structure

Use this structure unless the local repository has a clearer convention.
This structure is based on the OpenAI Codex Agent Skills documentation, Codex
best practices, and the open Agent Skills specification:

- [OpenAI Codex Agent Skills](https://developers.openai.com/codex/skills)
- [OpenAI Codex best practices](https://developers.openai.com/codex/learn/best-practices)
- [Agent Skills specification](https://agentskills.io/specification)
- [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)

```text
skill-name/
|-- SKILL.md
|-- agents/
|   `-- openai.yaml
|-- assets/
|-- evals/
|-- references/
`-- scripts/
```

## Required Files

Every skill requires `SKILL.md` with YAML front matter and Markdown body.
Front matter must include:

- `name`: lowercase hyphenated skill name.
- `description`: concise trigger-rich summary of what the skill does and when
  to use it.

The `name` should match the parent folder.

## Optional Folders

- `scripts/`: executable repeatable checks or helpers. Use when deterministic
  reliability is needed or agents keep rewriting the same helper.
- `references/`: longer docs, rubrics, policies, vendor notes, and technical
  references loaded only when needed.
- `assets/`: templates, examples, schemas, starter files, and static resources
  used as inputs or output scaffolds.
- `evals/`: reusable trigger prompts or quality-evaluation examples. Use when
  activation behavior needs repeatable evidence; keep examples public-safe and
  free of secrets or customer data.
- `agents/`: OpenAI metadata. Upstream Codex treats `agents/openai.yaml` as
  optional, but this repository requires it for source-owned skills so UI
  metadata, default prompts, dependencies, and invocation policy can be
  validated. Use `agents/openai.yaml`, not `agents.openai.yaml`.

## SKILL.md Section Template

Use the smallest section set that makes the workflow clear:

```markdown
---
name: skill-name
description: Use this skill when...
---

# Skill Name

## Purpose

## Use This Skill For

## Inputs Accepted

## Non-Goals

## Workflow

## Guardrails

## Learning Loop

## Validation

## Output Contract

## References
```

For larger skills, keep only core routing and workflow instructions in
`SKILL.md`; move long checklists and examples into `references/` or `assets/`.
For scaffolded skill folders, draft skill content, or update work, read
`references/skill-authoring-best-practices.md` after target scope is known.

## OpenAI Metadata

Use this exact path:

```text
skill-name/
|-- SKILL.md
`-- agents/
    `-- openai.yaml
```

OpenAI Codex uses `agents/openai.yaml` for optional UI metadata, invocation
policy, and tool dependencies. In this repository, every source-owned skill
must include it with at least an `interface.default_prompt` and
`policy.allow_implicit_invocation`.

Start from `assets/openai-agent-metadata.yaml.template`:

```yaml
interface:
  display_name: "Human Name"
  short_description: "Short user-facing summary"
  default_prompt: "Use $skill-name to do the repeatable workflow."
policy:
  allow_implicit_invocation: true
```

Set `allow_implicit_invocation` from the skill contract:

- `true`: ordinary reusable workflow skills that Codex may choose when the user
  prompt matches the front matter `description`.
- `false`: skills that must be explicitly requested by the user or a workflow
  coordinator, including Git commit/push/PR/merge flows, release/publish flows,
  auth or local setup, security mutation, container attachment, external MCP
  installation, workflow verification harnesses, and all `sdlc-*` Agentic SDLC
  phase skills.

If `SKILL.md` says the skill should run only after an explicit request, reflect
that in `agents/openai.yaml`; do not rely on prose alone. If the policy is
unclear, keep the change report honest and ask for the intended invocation
contract before setting the file.

For non-listed skills that are explicit-only, make the rule machine-checkable:
put wording such as `Use only when the user explicitly asks...` in the front
matter `description`, or add a concise `## Invocation Policy` section stating
that explicit invocation is required. Avoid treating ordinary safety guardrails
for one destructive action as a reason to disable implicit invocation for the
whole skill.

## Stateful Workflow Skill Profile

Use this opt-in profile for skills that manage local state, locked plans,
evidence, continuation prompts, retries, or failure routing. Do not force this
profile onto simple instruction-only skills.

Template: `assets/stateful-workflow-skill-template.md`

Required sections for the `stateful-workflow` validation profile:

- `## Purpose`
- `## When To Use`
- `## When Not To Use`
- `## Inputs`
- `## Required Reads`
- `## Writes`
- `## Process`
- `## Idempotency`
- `## Failure Handling`
- `## Must Not`
- `## Completion Criteria`
- `## Output Contract`

For this profile:

- State whether execution artifacts are committed project truth or private
  local state.
- Describe rerun behavior so agents do not duplicate plans, evidence, commits,
  or external resources.
- Classify failures before retrying or routing backward.
- Keep hooks as invariant guardrails; do not make hooks own workflow
  orchestration.
- Use MCP servers for external capabilities when available, while preserving
  explicit safety checks for write operations.

## Naming Rules

- Use lowercase letters, numbers, and hyphens.
- Do not start or end names with a hyphen.
- Do not use consecutive hyphens.
- Keep the folder name and front matter `name` identical.
- Prefer specific names that describe the job, not broad names such as
  `helper`, `tools`, or `automation`.
- For skills that are only valid inside the Agentic SDLC state machine, use an
  `sdlc-` prefix in the folder and front matter name. The coordinator uses
  `sdlc-start`, and phase skills use names such as `sdlc-commit` instead of
  broad generic names like `commit`.
- SDLC-only skill descriptions must start with
  `Use only as part of the Agentic SDLC workflow;` so tool discovery makes the
  workflow boundary explicit.
- Do not keep unprefixed aliases or compatibility wrapper folders for renamed
  SDLC-only skills unless the user explicitly requests a compatibility layer.

## Content Placement

- Put always-needed routing, scope, workflow, guardrails, and output contract in
  `SKILL.md`.
- Put the standard `## Learning Loop` section in every `SKILL.md` so durable
  public-safe source learning is active whenever that skill loads.
- Put long rubrics, policy details, vendor research rules, troubleshooting, and
  detailed examples in `references/`.
- Put templates, starter files, report outlines, schemas, or sample payloads in
  `assets/`.
- Put reusable trigger prompts and quality-evaluation examples in `evals/`.
- Put deterministic checks or reusable helpers in `scripts/`.

Avoid duplicating the same rule in multiple files. Link from `SKILL.md` to the
supporting file and state when to read it.

## Acceptable Single-Skill Layouts

Instruction-only skill:

```text
skill-name/
`-- SKILL.md
```

Skill with metadata:

```text
skill-name/
|-- SKILL.md
`-- agents/
    `-- openai.yaml
```

Skill with supporting resources:

```text
skill-name/
|-- SKILL.md
|-- assets/
|   `-- report-template.md
|-- evals/
|   `-- trigger-prompts.csv
|-- references/
|   `-- policy.md
`-- scripts/
    `-- validate.py
```

## Acceptable Multi-Skill Layouts

Flat parent folder:

```text
skills/
|-- first-skill/
|   `-- SKILL.md
`-- second-skill/
    `-- SKILL.md
```

Repository-local skills folder:

```text
.agents/
`-- skills/
    |-- first-skill/
    |   `-- SKILL.md
    `-- second-skill/
        `-- SKILL.md
```

For GitHub repositories or tree URLs, first detect whether the provided path is
a single skill or a parent folder containing multiple skills before proposing
changes.
