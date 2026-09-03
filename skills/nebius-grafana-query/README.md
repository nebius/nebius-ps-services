# Nebius Grafana Query

This skill answers read-only Nebius observability questions through an
already-configured `grafana-nebius` Grafana MCP server. It is implicitly
invokable, so natural-language metrics, logs, traces, datasource, and
dashboard-query questions can select it without an explicit skill mention.

## Boundary

The names describe different layers:

- `$nebius-grafana-query` is the Codex skill that defines the safe query
  workflow.
- `grafana-nebius` is the MCP server entry that exposes Grafana tools.

The skill never installs or configures the MCP server, repairs authentication,
changes IAM, issues static keys, or mutates Grafana. Missing server,
configuration, or authentication belongs to the explicit
`$install-grafana-mcp-for-nebius` workflow for direct user queries. When
embedded by `troubleshoot` or `sdlc-evaluate`, the same failure returns
`unavailable` and disables observability for that workflow run without setup or
retry.

The installer-owned wrapper authenticates every request as one pinned human
Nebius CLI user. The query skill never calls the Nebius CLI, uses the
project-scoped Codex agent identity, or switches to service/static credentials.

## Workflow

The skill accepts one or many explicit tenant, project, and resource scopes,
resolves one absolute UTC query window, lists accessible datasources, discovers
their labels/tags, and proves federation before using a single-datasource fast
path. Otherwise it decomposes work by datasource and scope. Different scope
kinds use `AND`; multiple values within one kind use `OR`. A missing required
dimension fails closed instead of becoming a broad query.

Metrics and logs prefer native Prometheus and Loki tools. A native-tool
availability or known decode defect permits only a fixed-path GET datasource
proxy for a discovered signal-compatible UID. Logs default to counts, with at
most 20 explicitly requested sanitized lines. Traces prefer proxied `tempo_*`
tools and use the same strict GET-only fallback rule when those tools are
unavailable. All signals share fixed scope, result, concurrency, timeout, and
total-deadline bounds.

An expensive combined query may be decomposed after a backend `5xx` only when
the branches are disjoint and can be recombined exactly under the same window,
predicates, datasource path, and credential. This does not widen the fallback
or retry policy.

## Evidence Provider

`troubleshoot` and `sdlc-evaluate` may invoke this skill as a bounded evidence
provider after they establish that runtime telemetry can change a named
hypothesis or predefined operational criterion. Both consumers record
non-Grafana provenance for one matching signal and pass one structured
`signal_fit`; evaluation also passes one `criterion_fit` with the exact
measurement, candidate/control attribution, pass/fail/inconclusive rules,
coverage, and grade relation. A missing or malformed fit is rejected before
readiness, which is never speculative signal or grading-rule discovery. The
caller supplies explicit authority, deterministic deployed selectors, absolute
candidate and baseline windows, and a remaining budget.

The first bounded datasource listing doubles as the only connectivity and
readiness check for one investigation or evaluation run. A failed check makes
observability unavailable for the rest of that run. The provider never invokes
the installer, repairs authentication, switches credentials, or repeats the
check.

The fast path has a cumulative allowance of at most six symptom-matched data
queries. A deep path has a separate cumulative allowance of at most four
decision-specific queries only when fast evidence leaves named
decision-relevant alternatives indistinguishable and the next query can change
the caller's decision. Every embedded provider invocation attempts at most one
pre-admitted data query and returns for a hypothesis or criterion-ledger update
before another query can be admitted; remaining budget is never a query target.
Results are a fixed redacted facts envelope with
scope, windows, anomalies, fingerprints, affected dimensions, observed
changes, correlations, representative events, data gaps, and query cost. The
envelope returns updated connectivity and total, fast, and deep remaining
budgets; invalid relevance, authority, selector, window, or budget returns
`rejected` with zero tool calls and unchanged state. The provider never claims
root cause or grades acceptance.

## Result Reports

Reports lead with the outcome, then show the absolute query scope, a ranked
result table, coverage/access, and method/limitations. Explicit user grouping
and ordering win. Otherwise the skill groups by the entity named in the
request, sorts problem-first on the requested field, uses stable scope
tie-breakers, and displays the top 20 with total and omitted counts.

Statistics are signal-aware:

- gauges show per-group minimum, average, and maximum over the fixed window;
- counters show increase or rate rather than raw cumulative averages;
- logs show counts, rates, and exact first/last occurrence when a supported
  bounded method provides it, with numeric statistics only for an extracted
  numeric field;
- traces show count/error information, exact full-window duration minimum,
  average, and maximum when available, and supported full-window quantiles with
  approximation semantics disclosed; and
- dashboards and datasource inventories omit meaningless statistics.

Coverage/access evidence is reported separately from native-tool, fallback,
decomposition, and backend health.

## Examples

```text
Show CPU and memory usage for the nodes in MK8s cluster <MK8S_CLUSTER_ID> in
project <PROJECT_ID> over the last 24 hours. Group by node, sort the requested
field problem-first, and show per-node minimum, average, and maximum.
```

```text
Check whether the eight-GPU nodes in project <PROJECT_ID> had any NVLink errors
in the last 7 days. Return counter increases and rates by node, not raw counter
averages.
```

```text
Summarize recent error logs for resource <RESOURCE_ID> in project <PROJECT_ID>
over the last hour. Group by node and return count, rate, and exact first/last
seen when available; include short sanitized examples only if I request them.
```

```text
Search traces for service <SERVICE> across projects <PROJECT_ID_1> and
<PROJECT_ID_2> for the last hour. Group by operation and return count, error
rate, exact full-window duration minimum, average, and maximum when available,
and a supported full-window p95 with its approximation semantics disclosed.
```

## MCP Flow

```text
User prompt
  -> Codex selects nebius-grafana-query
  -> the skill validates explicit scopes and chooses read-only tools
  -> grafana-nebius uses the pinned human token
  -> Grafana and authorized datasource APIs answer
  -> Codex returns an attributed, bounded, sanitized summary
```

## Files

- `SKILL.md`: trigger, runtime boundary, workflow, guardrails, and output.
- `agents/openai.yaml`: implicit invocation and UI metadata.
- `references/query-guide.md`: detailed discovery and query guidance.
- `references/result-reporting.md`: report format, grouping, ordering,
  signal-aware statistics, and coverage disclosure.
- `references/evidence-provider.md`: embedded request and result contracts,
  one-time readiness, fast/deep budgets, and consumer boundaries.
- `evals/trigger-prompts.csv`: canonical activation and routing examples.
- `evals/process-cases.md`: supplemental query and provider-boundary cases.
- `evals/evidence-provider-tool-traces.json`: fixed-envelope zero-call,
  readiness-reuse, cumulative-stage-budget, exact-cost, failure, and
  whole-envelope redaction scenarios.
