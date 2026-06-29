# Source Priority Rubric

Use this reference when a brainstorming topic needs context beyond the user's
initial prompt, especially for architecture, product, migration, cloud,
customer-impacting, or cross-repo questions.

## Context Intake

Summarize the user's topic into a compact search brief:

- topic and goal
- likely system, service, project, or product area
- keywords and synonyms
- relevant paths, links, tickets, channels, skills, products, or vendors
- decisions the user appears to be evaluating
- constraints, non-goals, and urgency when stated

Use that brief to guide searches. Do not turn brainstorming into broad
repository indexing.

## Source Order

### 1. Current Project Folder

Treat the current project folder as the first source of truth. Look for:

- `AGENTS.md`, `README.md`, `CHANGELOG.md`, and local docs
- `docs/requirements.md`, `docs/design.md`, architecture notes, ADRs, and runbooks
- source code, tests, fixtures, examples, generated reports, and CLI help
- config files, manifests, schemas, workflows, and deployment artifacts

Use targeted commands first:

```bash
rg --files
rg -n "keyword|synonym|symbol"
```

Open small ranges around hits. Prefer exact local evidence over assumptions or
older memory.

### 2. Sibling Project Folders

Escalate to sibling folders in the same repository when:

- the current project has no direct answer
- a shared pattern likely exists elsewhere
- the topic crosses services, charts, docs, workflows, or shared libraries
- the user asks for consistency with the rest of the repo

Search by topic keywords plus nearby naming conventions. Compare patterns
without proposing repo-wide changes during the brainstorm.

### 3. Related Skills

Use related skills as curated domain context, not as automatic execution
permission. Good candidates are explicitly named skills such as `$nebius`,
skills whose description matches the topic, and repo-owned skills that define a
workflow boundary.

For major design or architecture decisions, treat `design` and
`system-design-rules` as decision-advisory related skills when they are
installed and accessible. Use `design` to understand whether the discussion is
ready for a concrete design or `/plan` handoff, and use
`system-design-rules` to test tradeoffs, boundaries, ownership, failure modes,
and validation gaps. If either skill is unavailable, skip it and make the
skipped consultation explicit in the brainstorm answer.

Load only:

- the related skill's `SKILL.md`
- directly relevant references named by that skill
- assets or scripts only when their content clarifies the question

Respect the related skill's invocation policy and guardrails. If the related
skill is explicit-only, use it as a context source only unless the user
explicitly invokes it.

### 4. Internal Knowledge Sources

Use internal Confluence, Jira, Slack, and company docs when local sources leave
gaps about decisions, ownership, customer requirements, production incidents,
or historical rationale.

Search order:

1. Installed connectors or apps exposed in the current Codex surface.
2. Configured MCP servers exposed through tool discovery.
3. User-provided links or pasted excerpts.

Keep internal searches targeted. Prefer source titles, page names, ticket IDs,
channel names, dates, and concise paraphrases over copied blocks. Do not write
internal findings into public reusable skill source materials.

### 5. Official Vendor Documentation

Use current official vendor docs for products, clouds, APIs, SDKs, CLIs,
package managers, frameworks, standards, security controls, and service
limits.

Rules:

- Prefer official docs, API references, release notes, and official GitHub
  repos from the vendor.
- Use vendor-specific MCP/doc tools when available, such as OpenAI docs,
  Microsoft Learn, Context7, Terraform Registry, or provider docs.
- Use web search only when a suitable doc connector is unavailable or
  insufficient, and prefer official domains.
- Mark behavior as unverified when official docs do not confirm it.

## Conflict Handling

When sources disagree:

- Prefer current project behavior for repo-specific wrappers and local
  contracts.
- Prefer official vendor docs for external product behavior unless repo code is
  intentionally wrapping or constraining that behavior.
- Prefer current internal docs over stale Slack threads for durable decisions,
  but use Slack as useful history when docs are absent.
- State the conflict, what was preferred, and what remains uncertain.

## Answer Shape

A useful brainstorm answer usually contains:

- a one-paragraph topic restatement
- a compact source map: checked, relevant, missing
- facts separated from interpretations
- assumptions and hypotheses labeled
- 2-4 options with tradeoffs
- direct challenges to weak assumptions
- open questions that would materially change the recommendation
- suggested next workflow only when the user wants to move from discussion to
  action

Keep raw logs, broad source lists, and long copied excerpts out of the chat.
