# Research Methodology

Use this reference when a research request needs more than a quick lookup.
Scale depth to risk, but keep the investigation evidence-backed and useful for
engineering decisions.

## Intake

Turn the prompt into a compact research brief:

- subject and exact keywords
- project or system context
- decision the research should inform
- expected depth: light, standard, or deep
- constraints, non-goals, timeline, and audience
- source hints: links, RFC numbers, repos, docs, tickets, or examples
- internal-source scope: Slack channels, Confluence spaces/pages, project names,
  incident/runbook keywords, or owners to search when organization context
  matters

Ask a clarifying question only when missing context changes the source plan or
recommendation. Otherwise proceed with a stated assumption.

## Depth Levels

Use `light` for a narrow concept, API behavior, or quick alternative check.

Use `standard` for adoption decisions, architecture patterns, feature design,
operations, scaling, or multiple alternatives.

Use `deep` for platform choices, security-sensitive topics, protocols, data
ownership, high-scale systems, expensive migrations, compliance, or
hard-to-reverse decisions.

## Source Priority

Use this order for every non-trivial research request. Always consider internal
context first; search it when the question may depend on organization policy,
local deployment, runbooks, incidents, ownership, prior decisions, or accepted
exceptions. If internal context is clearly irrelevant, record that it was
skipped as not relevant.

1. Internal sources first when organization context matters. Search available
   agent connectors for Slack channels and Confluence pages to find local
   policy, runbooks, architecture decisions, incident history, project
   conventions, owner intent, and accepted exceptions.
2. Alternative internal connectivity. If agent connectors are not configured or
   available, use the appropriate MCP servers or app tools for the required
   internal systems when available and authorized. If no internal access exists,
   record the gap and continue with external research.
3. External vendor verification. Verify technology behavior against official
   vendor documentation, specifications, RFCs, standards, official source code,
   release notes, and API references.
4. Authoritative fallback. When official docs are missing or insufficient, use
   maintainer discussions, academic papers, credible postmortems, established
   engineering publications, and other reputable sources. Treat community
   reports as leads unless corroborated.
5. Cross-check. Reconcile internal guidance with external facts and label which
   claims are organization-specific, vendor-documented, or general industry
   practice.

Internal evidence can decide "what we do here" and "why this project has a
local exception." Vendor evidence decides upstream product behavior unless
internal sources document a fork, wrapper, configuration override, or tested
environment-specific behavior.

## External Source Tiers

### Tier 1: Authoritative

Use these as the foundation:

- official documentation
- RFCs, standards, and specifications
- vendor documentation and API references
- academic papers
- official GitHub repositories, source code, release notes, and design docs

### Tier 2: Implementation Knowledge

Use these to understand real implementation and maintainer intent:

- vendor or maintainer engineering blogs
- conference talks from maintainers or operators
- maintainer issues, pull requests, design proposals, and discussions
- credible production postmortems or operator writeups

### Tier 3: Community Knowledge

Use these only as leads and operational color:

- Reddit
- Stack Overflow
- Medium
- personal blogs
- forums and chat archives

Tier 3 is useful for hidden limitations, production pain points, and common
pitfalls. It is not authoritative. Verify important claims against Tier 1 or
Tier 2 sources before recommending action.

## Phase Checklist

### Phase 1: Understand The Question

Determine:

- What are we trying to learn?
- Why do we need it?
- What decision will this support?
- How deep should the research go?
- What would change the recommendation?

### Phase 2: Search Internal Context

Answer:

- What has the organization already decided about this topic?
- What Slack discussions, Confluence pages, runbooks, tickets, or incident
  notes are relevant?
- Are there local constraints, ownership rules, exceptions, wrappers, forks, or
  tested environment-specific results?
- If Slack/Confluence connectors are unavailable, is there an authorized MCP
  server or app connector for the same internal system?
- Which internal source classes were checked, unavailable, or skipped?

Summarize internal findings without copying broad excerpts. Do not include
secrets, private endpoints, internal hostnames, customer data, or proprietary
material beyond what is necessary and safe for the current response.

### Phase 3: Build Foundational Understanding

Answer:

- What problem does it solve?
- Why was it created?
- What existed before?
- What vocabulary, primitives, or assumptions must the user understand?

### Phase 4: Study Internals

Answer:

- How does it work?
- How is it implemented?
- What are the major components?
- What protocols, algorithms, data structures, or state machines are involved?
- What source files, interfaces, specs, or diagrams prove the behavior?

### Phase 5: Study Operations

Answer:

- How is it deployed?
- How is it configured?
- How is it monitored?
- How does it fail?
- How does it scale?
- How does it upgrade, recover, and interact with security boundaries?

### Phase 6: Study Limitations

Answer:

- What breaks?
- What does not scale?
- What are common pitfalls?
- What do maintainers warn about?
- What compatibility, maturity, ecosystem, cost, or support limits exist?

### Phase 7: Study Alternatives

Answer:

- What competes with it?
- What simpler or native option might be enough?
- Why choose this over alternatives?
- When should it not be used?
- What are the switching costs and revisit conditions?

### Phase 8: Cross-Check And Generate Recommendations

Answer:

- What internal guidance agrees with vendor documentation?
- Where do local practices differ from vendor recommendations or general
  industry practice?
- Which claims are vendor-documented, organization-specific, or unverified?
- Should we use it?
- For which use cases?
- What risks should shape the design?
- What should we avoid?
- What proof of concept, test, benchmark, or operational check is needed next?

## Conflict Handling

When sources disagree:

- Prefer current official docs over stale articles.
- Prefer internal sources for local policy, ownership, runbooks, and historical
  decisions, but verify technical product behavior externally.
- Prefer specifications for protocol semantics.
- Prefer source code for actual implementation behavior.
- Prefer maintainer issues or design proposals for intent and known limits.
- Treat community reports as signals to investigate, not final evidence.

State the conflict, what source tier you trusted, and what remains uncertain.

## Report Template

Use this structure unless the user asks for a shorter format:

### Executive Summary

State the main finding, recommendation, confidence, and decision impact.

### Source Coverage

Summarize what was checked and what was unavailable:

- Internal sources: Slack, Confluence, tickets, runbooks, or internal docs.
- Internal access path: agent connectors, MCP/app tools, unavailable, or
  skipped as not relevant.
- External sources: official vendor docs, specs, source code, release notes,
  maintainer discussions, or reputable fallback sources.
- Provenance labels used in the report: organization-specific guidance, vendor
  documentation, general industry practice, and unverified claims.

### Core Concepts

Define the primitives, vocabulary, and mental model needed to understand the
topic.

### Architecture

Describe major components, boundaries, data flow, control flow, protocols, and
deployment shape.

### Internal Mechanics

Explain how the important behavior is implemented. Cite source code, specs, or
official architecture docs when available.

### Design Principles

Explain the constraints and design choices that shaped the technology.

### Use Cases

List the cases where it fits well and the assumptions behind those fits.

### Advantages

Name concrete benefits, not generic praise.

### Disadvantages

Name costs, complexity, operational burden, coupling, maturity gaps, and
failure modes.

### Operational Considerations

Cover deployment, configuration, monitoring, scaling, security, upgrades,
compatibility, and day-2 support.

### Limitations

Call out hard limits, weak spots, unsupported cases, maintainer warnings, and
unverified claims.

### Alternatives

Compare realistic alternatives by tradeoff, complexity, maturity, operating
cost, and revisit condition.

### Recommendations

State what to do, what not to do, risks to manage, and next validation steps.

### References

Group references by provenance and source tier. Include source names, links or
stable internal identifiers when safe, and the claim each source supports.
Never include secrets, private endpoints, internal hostnames, customer data, or
large raw excerpts from internal systems.

## Quality Bar

- Do not stop at a definition.
- Do not use unranked source dumps.
- Do not hide uncertainty.
- Do not turn community anecdotes into architecture facts.
- Do not recommend adoption without explaining the conditions where the
  recommendation fails.
