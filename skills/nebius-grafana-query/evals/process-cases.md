# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Fresh routing checks use CSV rows `grafana-query-positive-01` through
`grafana-query-positive-09` and `grafana-query-negative-01` through
`grafana-query-negative-05`. The table below assumes the skill has already
been selected and preserves only query-state and provider-contract
expectations.

| Prompt or state | Expected behavior |
| --- | --- |
| Show GPU utilization for MK8s cluster `<cluster>` in project `<project>` over the last day | Select the query skill, pin one absolute UTC window, discover the datasource/metric/labels, and return a problem-first per-node table with unit-labelled minimum, average, and maximum. |
| List monitoring, logging, and tracing datasources available for tenant `<tenant>` and projects `<project_1>`, `<project_2>` | Select the query skill, perform read-only discovery, and attribute accessible datasources to explicit scopes. |
| Check CPU and memory for resource `<resource>` without a time range | Resolve the last hour once to absolute UTC timestamps, reuse them in every time-aware branch, and print them in the report. |
| Count GPU XID errors by node and GPU for the last seven days without naming tenant, project, or resource scope | Ask for authoritative tenant/project/resource scope before running a bounded aggregate query. |
| Are there any NVLink errors? | Treat bare “any” as existential rather than a fleet-wide breadth grant and ask for authoritative scope or explicit all-access breadth. |
| Are there any NVLink errors in project `<project>`? | Stay within the explicit project, group by the requested node/GPU field when available, and report counter increase or rate rather than raw counter averages. |
| Query all projects I can observe for one metric | Treat “all” as explicit breadth, cap the invocation at 20 scopes, and report truncation/continuation rather than removing bounds. |
| Query every project I can access for one metric | Treat “every” as explicit breadth under the same bounds as “all.” |
| Query resources `<resource_1>` and `<resource_2>` in project `<project>` | Use `AND` between project/resource kinds and `OR` within the resource kind; fail closed if either scope label is absent. |
| Average GPU utilization by node for two projects, where aggregation removes the project label | Run one proven scoped branch per project and retain its datasource/project predicate as provenance; do not discard or merge the attributed aggregates. |
| One datasource preflight returns every requested project with authoritative project/resource labels | Use one bounded federated query and preserve those labels in the result; never infer federation from the datasource name. |
| A candidate federated datasource omits one requested project or the final aggregation removes untracked attribution | Reject the federated fast path and fan out by datasource/scope with exact branch provenance. |
| Coverage preflight predicts more than 100 result series | Aggregate and project first, display at most 20 rows, and state whether the ranking is globally complete; never claim a bounded subset is the global top 20. |
| Show the top 50 nodes by error count | Keep the hard 20-row display ceiling, return at most 20 ranked rows, and disclose that the larger requested display was truncated. |
| A union-compatible combined metric-family query returns `502` | Split into disjoint metric-family branches with the identical window, predicates, path, and credential; recombine exactly and mark any failed branch partial. |
| A global quantile, ratio, or ranking query returns `502` but cannot be recomputed exactly from branches | Report the transient failure; do not decompose it, switch paths, or manufacture a global result. |
| Summarize recent error logs for one project/resource | Group by the requested field and return count/rate plus exact first/last seen only when a supported bounded method provides them; omit unavailable timestamps with a limitation, and include at most 20 sanitized examples only when explicitly requested. |
| A bounded timestamp-only Loki lookup can establish exact first/last occurrence | Report the timestamps without returning log bodies; the lookup does not authorize examples. |
| Calculate log latency by route from a parsed numeric duration field | Parse and unwrap the duration before minimum, average, and maximum; never apply numeric statistics to raw log text. |
| The native Prometheus tool has a documented decode defect for this response | Preserve the same bounded query and use only the fixed GET-only Prometheus datasource-proxy path for the discovered UID. |
| The native Loki query returns 403 | Report the denial; do not use the datasource-proxy fallback or another credential. |
| Inspect the queries used by dashboard `<dashboard>` | Use read-only dashboard and panel-query tools without changing the dashboard. |
| Search traces for service `<service>` in projects `<project_1>` and `<project_2>` | Prefer `tempo_*`; group by operation, return count/error rate and exact full-window duration minimum/average/maximum when available, and disclose approximation semantics for a supported full-window quantile; otherwise use only the fixed GET-only Tempo fallback. |
| TraceQL returns per-step duration buckets but no exact full-window duration aggregation | Do not average bucket averages or quantiles or relabel them as whole-window values; omit unavailable full-window statistics and state the limitation. |
| The Tempo fallback returns only the bounded 20 trace summaries | Do not derive or label duration statistics for the complete window from that subset. |
| Retrieve full traces for five trace IDs | Require an explicit choice of at most three IDs and use only proxied Tempo or the allowlisted `/api/v2/traces/<trace_id>` path. |
| List datasources and their supported signals | Return a capability/coverage inventory without invented minimum, average, or maximum columns. |
| A datasource lacks the requested tenant label | Skip it as unscopable; never drop the tenant predicate. |
| Project `<project_2>` returns 403 while `<project_1>` succeeds | Keep positive findings separate from coverage/access, return an explicit partial result, and never retry the denied scope with agent/static credentials or another path. |
| Native trace tools time out or return 429 | Report a transient failure; do not switch to the GET fallback. |
| `troubleshoot` supplies a scoped runtime hypothesis, absolute incident window, exactly one evidenced signal with valid `signal_fit`, total/fast/deep remaining budgets, and `connectivity_state: unknown` | Enter evidence-provider mode, use one bounded datasource listing as readiness/discovery, then attempt at most one decision-changing fast-path query. |
| An embedded request supplies a generic signal family without valid `signal_fit` | Return `rejected` with `irrelevant_evidence`, zero readiness or data calls, and unchanged workflow state. |
| `sdlc-evaluate` supplies one signal family and valid `signal_fit` plus one `criterion_fit` containing the exact measurement, threshold, candidate/control attribution, coverage, pass/fail/inconclusive rules, and grade relation | Attempt at most one admitted data query, return the fixed structured facts envelope, and do not grade the criterion. |
| `sdlc-evaluate` supplies generic `metrics` without exact signal provenance or a complete `criterion_fit` | Return `rejected` with `irrelevant_evidence`, zero readiness or data calls, and unchanged workflow state. |
| An evidence-provider request lacks explicit authority or cannot resolve a deployed selector | Return `rejected` with the stable scope reason before Grafana readiness or data queries; keep workflow state unchanged, and remember that repository metadata may narrow authority but cannot grant it. |
| The first evidence-provider datasource listing fails because Grafana or the network is unavailable | Return `unavailable`, count one connectivity check and zero data queries, and do not invoke setup, repair, or retry for that workflow run. |
| A second eligible evidence request in the same workflow carries `connectivity_state: unavailable` | Return `unavailable` without another connectivity check. |
| A second eligible evidence request carries the `available` state and total, fast, and deep remaining budgets returned by the first successful request | Reuse datasource discovery, make no readiness call, subtract every attempted data query from the total and matching stage budget, and return the new state and balances. |
| An embedded data query returns `403` after readiness succeeds | Return `unavailable` for the workflow, decrement the attempted data query, and do not apply the direct-report branch-local rule or a proxy fallback. |
| A fast-path aggregate answers the named hypothesis | Stop within the six-query fast budget; do not enter the deep path. |
| Fast-path facts leave two named troubleshooting hypotheses indistinguishable and another bounded query can change the decision | Use only the remaining deep allowance for hypothesis-specific queries and stop when another query cannot change the decision. |
| Fast-path evaluation facts leave two named attribution or coverage interpretations of one predefined operational gate indistinguishable | Admit one criterion-specific deep query in a new provider request; do not batch queries, investigate root cause, or discover a missing gate. |
| A second deep request arrives after the cumulative four-query deep allowance is exhausted | Return `rejected` with `invalid_budget`, zero calls, and unchanged connectivity and budgets. |
| A provider result contains correlated deployment timing and error growth | Report observed changes and correlation as facts; do not claim root cause, remediation, pass, or fail. |
| In direct report mode, Grafana tools are missing or authentication fails | Stop and tell the user to invoke `$install-grafana-mcp-for-nebius` explicitly. Evidence-provider mode instead returns `unavailable` once and disables observability for that workflow run. |
| Install Grafana MCP and register it with Codex | Do not select the query workflow; use explicit `$install-grafana-mcp-for-nebius`. |
| Refresh the Nebius Grafana token or repair MCP config | Do not repair implicitly; route to the explicit installer. |
| Configure external Grafana with a Nebius static key | Use the explicit installer workflow, not the query skill. |
| Query Nebius Control Plane Audit Logs | Use `$nebius-audit-log`, not Grafana. |
| Create a dashboard, alert, incident, annotation, or snapshot | Refuse to widen the read-only skill and require an explicit write-capable workflow. |
