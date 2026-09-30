# README Design

## Purpose

Design `README.md` as the project's concise entry point for people evaluating,
using or contributing to it. Help readers understand the project, reach a first
successful result and find the right deeper documentation.

Apply this reference within a design for a new project, a material change to
project usage, or explicit README creation or restructuring. Routine wording,
link or TOC maintenance alone does not require the software-design workflow.
This reference grants no write authority: planning returns an outline and
documentation changes for the implementation handoff. Canonical requirements
and design publication remains with `maintain-project-specs`.

## Reader Journey

Arrange content to answer these questions in order:

1. What is this, and who is it for?
2. Why would I use it?
3. Can I use it with my environment and constraints?
4. How do I reach the first successful result?
5. How do I configure and use it for common work?
6. Where do I learn more, contribute or get help?

Put the shortest path to first success before deep architecture. Make important
prerequisites, compatibility limits and production-readiness constraints visible
before readers follow affected instructions.

## Essential Content

Answer the reader's questions without requiring one heading per answer:

- Identify the project and its purpose in a concise introductory sentence.
- Explain the audience, problem and useful outcome. Add an Overview only when
  it contributes information beyond the introduction.
- State quick-start prerequisites, the working directory, minimum required
  configuration, commands and an observable successful result.
- Explain common usage and relevant configuration, then link to full references.
- Include verified support and contribution/security/license information when
  applicable. Never invent a support channel, license or policy file.

Inspect the selected project's code, CLI/help, manifests, configuration, tests
and existing documentation owners before proposing commands, defaults or links.
Use the project's established tooling. Verify version-sensitive external
instructions against current official documentation; mark unresolved facts.

For an existing implementation, report discrepancies and use code as the
current-state baseline without declaring defects correct. For a new project or
future feature, label the outline and examples as proposed. Do not claim planned
commands, compatibility or production readiness already exist. During authorized
implementation, verify the resulting behavior before presenting it as current.

## Recommended Structure

Use this as an adaptable outline. Include sections because readers need them;
remove irrelevant sections and their TOC entries. Keep each section focused on
one purpose and avoid repeating the introduction in Overview.

```markdown
# Project name

One concise sentence explaining what the project does and why it exists.

Optional: critical status, compatibility or readiness notice.

## Table of contents

- [Overview](#overview)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Architecture](#architecture)
- [Development](#development)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Security](#security)
- [Support](#support)
- [License](#license)

## Overview

Audience, problem and useful outcomes beyond the introductory sentence.

## Quick start

Prerequisites, minimum configuration, shortest workflow and expected result.

## Configuration

Important options, environment variables, files and defaults; link to details.

## Usage

Common workflows with verified, copyable examples.

## Architecture

Short component and flow summary; link to the owning design documentation.

## Development

Contributor setup; link to detailed development instructions when available.

## Testing

Relevant test and validation commands, prerequisites and execution scope.

## Troubleshooting

Common problems and immediate solutions; link to detailed runbooks.

## Contributing

Brief instructions or a link to the existing contribution policy.

## Security

Link to the existing security reporting policy when available.

## Support

Verified places to report problems or ask questions.

## License

Verified license name and link to the license file.
```

Essential setup belongs in Quick start even when a Configuration section follows.
Do not require readers to discover a mandatory variable several sections later.
If a project is a library, catalog or documentation collection, adapt first
success to its actual use; do not invent a service deployment or installation.

## Table of Contents

GitHub provides an automatic heading outline. Use a visible TOC near the start
as this skill's default for substantial engineering READMEs; a short README may
omit it. The ordering here is a skill default, not a GitHub platform requirement.

- Place it after the concise introduction and any critical status notice.
- List retained H2 sections in document order, excluding the title and TOC.
- Add H3 destinations only when useful enough to justify the extra entries.
- Match each label to its heading exactly and verify the generated anchor.
- Prefer descriptive, unique, simple headings; avoid duplicated or unstable
  headings that produce ambiguous anchors.
- Remove stale entries whenever a section is removed or moved. Keep the list
  easy to scan rather than reproducing every minor subsection.

## Writing and Formatting

- Use one H1 for the project, H2 for major sections and H3 for useful subdivisions.
  Do not skip levels. H4 or deeper usually warrants a separate document.
- Keep one idea per paragraph, normally in two or three concise sentences.
  Explain what and why before internal implementation details.
- Use lists for steps or enumerable information; use tables for comparative or
  multi-attribute data, such as configuration options, rather than simple lists.
- Prefer examples to lengthy explanations. Use language identifiers on fenced
  code blocks, omit shell prompts and separate output from runnable commands.
- Make placeholders obvious and explain every required substitution before use.
  Use public-safe examples; never include secrets, private endpoints or customer
  data. Document the intended secret store without showing secret values.
- Prefer relative repository-file and image links. Check their destinations from
  the README's directory. Link only to existing files in a delivered README;
  identify new destinations explicitly as planned in a design handoff.
- Use diagrams when they explain relationships more efficiently than prose.
  Give meaningful images useful alt text; explain essential diagram information
  in accessible text as well.
- Avoid decorative formatting, unverified badges and repeated content. Preserve
  useful existing information or move it to a named owner before replacing it
  with a link.

## Documentation Boundaries

Keep orientation, first success and common usage in the README. Use the existing
documentation layout rather than creating files solely to match these examples.

| Material | README treatment | Detailed owner when present |
| --- | --- | --- |
| Architecture and design rationale | Short components/flow summary | Design or architecture docs |
| Design-decision history | Link when helpful | ADRs |
| APIs, commands and configuration | Common examples and important defaults | Reference documentation |
| Operations, troubleshooting and recovery | Common immediate fixes and links | Runbooks |
| Development and contribution policy | Setup entry point and brief guidance | Development guide, `CONTRIBUTING.md` |
| Security reporting | Policy link | `SECURITY.md` |
| Release history | Changelog link | `CHANGELOG.md` |
| License | License name and link | `LICENSE` or the actual license file |

README documentation serves human orientation and use. Design documentation
explains architecture, boundaries, decisions and flows. Keep both connected
without copying one into the other. Include internal details only when a user
or contributor needs them to make a decision or complete a task.

## Project-Specific Sections

Add architecture, deployment, development, testing, compatibility, limitations
or production-readiness sections only when relevant to the intended audience.
Keep required safety or compatibility information near the affected quick-start
or usage instructions even if a later section gives more detail.

For architecture-heavy projects, include a short early summary if needed to
understand use, and link to the deeper explanation. Preserve existing project
conventions when they meet reader needs; document any useful ordering exception
in the plan rather than enforcing the template mechanically.

## Quality Checklist

- Can a new reader understand the purpose and audience in about a minute?
- Is the first successful workflow easy to find, with explicit prerequisites,
  minimum configuration, working directory and observable expected result?
- Are commands copyable and consistent with the inspected tooling and behavior?
- Are current implementation facts distinct from proposed or unverified content?
- Are headings descriptive and properly nested, and does the TOC match them?
- Do relative links and anchors resolve, and are images accessible?
- Are important limitations and readiness constraints visible before use?
- Does every retained section help readers without repeating another owner?
- Are support, security, contribution and license claims evidence-backed?

Validate links, anchors, Markdown rendering and commands with proportionate,
safe checks. Do not execute live or destructive quick-start actions merely to
validate documentation. Report what was checked and what remains unverified.

## Sources

Use GitHub's platform guidance for rendering and linking behavior. The section
order, visible TOC preference and H1–H3 default above are this skill's choices;
GitHub Docs' editorial conventions are supporting guidance, not universal rules.

- [About repository READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
- [GitHub Docs style guide](https://docs.github.com/en/contributing/style-guide-and-content-model/style-guide)
- [Creating and highlighting code blocks](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-and-highlighting-code-blocks)
- [Content design principles](https://docs.github.com/en/contributing/writing-for-github-docs/content-design-principles)
- [Create README example](https://docs.github.com/en/copilot/tutorials/customization-library/prompt-files/create-readme)
