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
- `agents/`: agent-specific metadata only when needed by the local repository
  convention. In this repository, use `agents/openai.yaml` for UI metadata.

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

## Naming Rules

- Use lowercase letters, numbers, and hyphens.
- Do not start or end names with a hyphen.
- Do not use consecutive hyphens.
- Keep the folder name and front matter `name` identical.
- Prefer specific names that describe the job, not broad names such as
  `helper`, `tools`, or `automation`.

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
