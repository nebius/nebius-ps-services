# Triggering Guide

This guide explains how to trigger `align-skill` across Codex surfaces using
only behavior confirmed by official OpenAI documentation.

Docs reviewed:

- [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)
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
- The initial skills list uses at most 2% of the model context window, or 8,000
  characters when the context window is unknown. The budget includes each
  skill's name, description, and path; when many skills are installed, Codex
  shortens descriptions first and may then omit skills from the list.
- Codex can activate a skill explicitly or implicitly.
- In ChatGPT, explicit invocation uses `@` to select a skill.
- In CLI/IDE, explicit invocation is available by running `/skills` or typing
  `$` to mention a skill.
- Implicit invocation depends on the `description` field matching the task.

## Description Strategy

The best trigger strategy is a precise, trigger-rich `description` field:

- Front-load the main job.
- Keep the description concise because every enabled repo, user, admin, system,
  and plugin skill shares the initial list budget.
- Include user phrases that should activate the skill.
- Include accepted inputs such as skill names, local folders, multi-skill
  folders, GitHub repositories, and GitHub tree URLs.
- Include authoring-helper intents such as refining, hardening, validating, or
  updating a draft or scaffolded skill.
- Include boundaries so the skill does not steal general codebase alignment
  tasks from `align` or initial scaffolding tasks from `skill-creator`.

## Discovery Locations

Official OpenAI docs state that Codex reads skills from repository, user,
admin, and system locations. For repositories, Codex scans `.agents/skills`
from the current working directory up to the repository root. User skills live
under `$HOME/.agents/skills`, admin skills can live under `/etc/codex/skills`,
and system skills are bundled by OpenAI.

When aligning a local repository, inspect the current source tree rather than
assuming the installed user-skill copy is the source of truth. When aligning a
user-installed skill, inspect the user-skill folder directly.

## Codex CLI

In the Codex CLI, start Codex in the repository or folder that can see the
target skills. Trigger reliably by mentioning `align-skill` and the target:

```text
Use align-skill to review and align `skills/foo`.
```

Confirmed CLI/IDE explicit mechanisms are `/skills` or typing `$` to mention a
skill. Official docs also describe explicit invocation as including the skill
directly in the prompt. Natural prompts can also work when the `description`
matches, but the most deterministic prompt includes `align-skill` plus the
target path, skill name, folder, GitHub repository URL, or GitHub tree URL.

## ChatGPT

Type `@` to select `align-skill`, then identify the target skill or folder in
the prompt. Keep report-only or remote-source boundaries explicit when ChatGPT
does not have an authorized writable local target.

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
