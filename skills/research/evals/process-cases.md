# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `research` routing behavior.
Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must produce focused senior-engineer due diligence with
  provenance labels, internals, operational behavior, limitations,
  alternatives, and actionable recommendations.
- Organization-specific questions inspect relevant local evidence, search
  connectors only for unresolved organizational context, and verify technical
  claims against current authoritative external sources.
- The skill must remain bounded to one focal topic rather than taking ownership
  of application-stack, AI-stack, design, implementation, checklist-review, or
  SDLC work.
- Negative rows must route to the named adjacent owner, with research returning
  only for a bounded disputed or unfamiliar claim.

## Manual Runtime Check

When routing precision matters, exercise `research-positive-01` through
`research-positive-11` and `research-negative-01` through
`research-negative-07` in a fresh Codex thread where the source skill is
installed or discoverable. If `research` steals ideation, design, stack
selection, implementation, checklist review, or SDLC tasks, narrow the front
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

## Adaptive Synthesis and Failure Cases

The quality JSON contains cases for narrow retrieval, substantial synthesis,
unenforceable hard constraints, ambiguous completion, stale/missing citations,
confidential context and hostile page instructions, early stopping, named-gap
expansion and unavailable/failed/partial jobs. Use the current working-byte
baseline for comparisons. Do not treat report-only answers as live tool proof.

For fresh native trials on each host, freeze the expected workflow first:

- A narrow official-documentation question must use the appropriate retrieval
  route and launch no Research job.
- A substantial public research question may launch at most one mini job in
  the trial; observe actual tool arguments and completion handling, then original
  source retrieval and the supported final claims.
- A pending job or timeout must not be resubmitted. No authentication repair,
  token copying, forced tool injection or intervention can count as native proof.

Isolated native evals do not inherit real-home MCP OAuth. Keep static, package,
fresh trigger, comparative quality and live-tool evidence separate. Unavailable
credentials or controls remain UNAVAILABLE; a supplied skill body does not prove
native discovery.
