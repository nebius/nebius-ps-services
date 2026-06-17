# Triggering Guide

This guide explains how to trigger `align-skill` across Codex surfaces using
only behavior confirmed by official OpenAI documentation.

Docs reviewed:

- [OpenAI Codex Agent Skills](https://developers.openai.com/codex/skills)
- [OpenAI Codex best practices](https://developers.openai.com/codex/learn/best-practices)
- [OpenAI Codex IDE extension](https://developers.openai.com/codex/ide)
- [OpenAI Codex app](https://developers.openai.com/codex/app)
- [Agent Skills specification](https://agentskills.io/specification)
- [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)

## Confirmed Behavior

Official OpenAI Codex documentation confirms:

- Codex Skills are available in the Codex CLI, IDE extension, and Codex app.
- Skills use progressive disclosure. Codex initially sees each skill's `name`,
  `description`, and file path, then loads the full `SKILL.md` only when it
  decides the skill is relevant.
- Codex can activate a skill explicitly or implicitly.
- In CLI/IDE, explicit invocation is available by running `/skills` or typing
  `$` to mention a skill.
- Implicit invocation depends on the `description` field matching the task.

No official OpenAI documentation reviewed for this skill confirms manual
`@skill` invocation syntax for skills. Do not document `@skill` or other manual
syntax unless current official OpenAI documentation confirms it.

## Description Strategy

The best trigger strategy is a precise, trigger-rich `description` field:

- Front-load the main job.
- Include user phrases that should activate the skill.
- Include accepted inputs such as skill names, local folders, multi-skill
  folders, GitHub repositories, and GitHub tree URLs.
- Include authoring-helper intents such as refining, hardening, validating, or
  updating a draft or scaffolded skill.
- Include boundaries so the skill does not steal general codebase alignment
  tasks from `align` or initial scaffolding tasks from `skill-creator`.

## Codex CLI

In the Codex CLI, start Codex in the repository or folder that can see the
target skills. Trigger reliably by mentioning `align-skill` and the target:

```text
Use align-skill to review and align `skills/foo`.
```

Confirmed CLI/IDE explicit mechanisms are `/skills` or typing `$` to mention a
skill. Natural prompts can also work when the `description` matches, but the
most deterministic prompt includes `align-skill` plus the target path, skill
name, folder, GitHub repository URL, or GitHub tree URL.

## Codex IDE Extension

The Codex IDE extension works with VS Code-compatible IDEs such as Visual
Studio Code, Cursor, and Windsurf, and official docs state it can read files,
run commands, and write changes in the project directory when running in Agent
mode.

Recommended flow:

1. Open the repository or target skill folder in the IDE.
2. Open the Codex panel.
3. Prompt Codex with the target path and explicit request:

   ```text
   Use align-skill to align `skills/foo`.
   ```

If the target skill is in the current workspace, prefer local paths over remote
URLs. If the target is remote, provide the GitHub repository or GitHub tree URL.
If multiple skills are involved, provide the parent folder path or list the
specific skill names. The skill should first detect target scope before making
changes.

## Codex App Or Desktop Local Workflow

Official docs describe the Codex app as a local workflow on macOS and Windows:
users select a project, choose Local, and send a message so Codex works on the
machine.

Recommended flow:

1. Open the local project or workspace that contains the skills.
2. Ensure the target files are in the selected project or otherwise available.
3. Mention `align-skill` explicitly for deterministic activation:

   ```text
   Use align-skill to review all skills under `skills/`.
   ```

The skill should still be discoverable through natural prompts because its
description contains trigger-rich terms. If runtime activation is not observed,
report trigger readiness from metadata inspection only.

## Example Prompts

```text
Use align-skill to review and align `skills/foo`.
Use align-skill as a helper after skill-creator scaffolded a release-triage skill.
Align these skills against the canonical structure and official vendor docs.
Review all skills under this folder and produce an alignment report.
Validate this GitHub skills repo and propose safe changes.
Fix the `SKILL.md` for this skill so it follows Codex Skill best practices.
Help me harden this draft `SKILL.md` into a safe, secure, fast Codex skill.
Check whether this skill has safe guardrails before live validation.
Standardize this multi-skill folder and add missing references, assets, or scripts.
Review this skill's vendor-specific commands against official documentation.
Align `skills/foo` and `skills/bar`, but do not run live tests unless the environment is confirmed as non-production.
```
