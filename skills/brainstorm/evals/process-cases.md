# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `brainstorm` trigger
behavior. Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must remain chat-only, gather only relevant source-ranked
  context, challenge assumptions, and return options plus open questions.
- Advisor skills may inform the discussion but must not take over the workflow
  or turn it into a final design or implementation plan.
- Bounded research may resolve a source conflict, after which control returns
  to the brainstorming discussion.
- Negative rows must route to the requested implementation, SDLC, review,
  publication, communication, research, design, or commit owner.

## Manual Runtime Check

When routing precision matters, exercise `brainstorm-positive-01` through
`brainstorm-positive-11` and `brainstorm-negative-01` through
`brainstorm-negative-07` in a fresh Codex thread where the source skill is
installed or discoverable. If `brainstorm` steals implementation, SDLC,
research, design, review, communication, or commit tasks, narrow the front
matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.

## Documentation Retrieval Cases

Exercise these output checks independently of trigger scoring, preserving the
skill's existing workflow and higher-priority host rules:

1. Vendor documentation tools are exposed in the current session: use those
   tools first. A server name in configuration alone does not count.
2. Vendor tools are absent, failing or insufficient; Tavily is available:
   use Search restricted to official domains, or Extract directly for a known
   official URL. A small documentation question follows this same route.
3. Tavily tools, authentication or coverage are insufficient: disclose the
   limitation and use built-in search or retrieval against official pages.
4. For an unrelated quick fact, built-in search remains appropriate. Avoid
   unnecessary Map, Crawl or Research calls and repeat queries.
5. Cite original pages, check the relevant version, keep confidential content
   out of public queries, and never install integrations or repair auth merely
   to satisfy a lookup. Context7 is not part of the prescribed route.

Record actual tool calls separately from instruction-level reasoning. Static
fixtures alone do not prove native tool access or model routing quality.
