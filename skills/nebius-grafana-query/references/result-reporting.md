# Nebius Grafana Result Reporting

Read this reference before returning results from `nebius-grafana-query`.
Apply the common report shape first, then the signal-specific rules.

## Required Report Shape

Return these sections in order:

### Outcome

Lead with the direct answer. State whether the result is positive, no data,
partial, denied, or unavailable. Do not bury a partial-coverage qualification.

### Query scope

State:

- the interpreted signal and primary result field;
- the absolute UTC start and end timestamps, plus the user's original relative
  phrase when applicable;
- explicit tenant, project, resource, and narrower selectors;
- whether broad enumeration was explicit;
- the applied grouping and sort direction; and
- the number of scopes planned.

Do not claim a tenant, customer, environment, or resource dimension that the
datasource did not prove.

### Results

Use a compact Markdown table. Put authoritative scope columns first, the group
key next, and the user's requested field as the first result measure. The
requested field is also the primary sort key.

Display at most 20 rows. Immediately above or below the table, state:

- total matched groups when known;
- displayed and omitted row counts;
- whether the ranking is globally complete; and
- any truncation or continuation boundary.

Do not dump raw series, logs, spans, or complete traces.

### Coverage and access

Keep coverage and authorization evidence separate from findings. Summarize
each scope or datasource as one of:

- queried successfully;
- queried successfully with no matching data;
- denied;
- skipped because a required dimension was absent;
- failed because of a transient or protocol error; or
- truncated by a documented bound.

A successful query proves access only to the returned datasource and
attributed scope. It does not prove universal tenant, project, or resource
access.

### Method and limitations

State:

- datasource UID and observed type;
- metric name and PromQL, LogQL, TraceQL, dashboard panel query, or guarded
  path category;
- native tool, guarded GET-only fallback, or semantics-preserving
  decomposition used;
- result limits, sanitization, and sampling decisions;
- failed decomposition branches and deduplication key when applicable; and
- limitations that affect accuracy or completeness.

Keep queries reviewable but remove secrets, authorization data, private base
URLs, and unrelated identifiers.

## Grouping And Ordering

Apply this precedence:

1. Honor explicit user grouping and ordering. Honor a requested display limit
   only up to the hard 20-row ceiling; truncate and disclose larger requests.
2. Otherwise group by one unambiguous entity named by the user, such as node,
   GPU, service, operation, resource, namespace, or project.
3. If no grouping is explicit or unambiguous, keep attributed rows separate;
   do not invent a grouping dimension.
4. Sort problem-first on the requested field:
   - descending for errors, failures, latency, saturation, utilization,
     throughput, and other high-is-worse or high-is-relevant measures;
   - ascending for health, availability, free capacity, headroom, and other
     low-is-worse measures; and
   - descending when polarity cannot be proved.
5. Break ties with the available authoritative scope identity in
   environment, tenant, project, resource, datasource, then group-key order.
   Omit dimensions that were not proven.

Group before sorting. Preserve scope attribution even when the requested
aggregation removes native scope labels.

## Statistics Window

Default minimum, average, and maximum values describe each result group over
the complete pinned absolute window. Label units and distinguish whole-window
statistics from per-step or per-bucket values.

If the backend can return only bucketed values, identify that limitation. Do
not relabel a bucket statistic as a whole-window statistic.

## Metrics

### Gauges

For a gauge or gauge-like numeric series, default to:

- requested value or most recent value when relevant;
- minimum over the pinned window;
- average over the pinned window;
- maximum over the pinned window;
- unit; and
- sample count or coverage information when available.

### Counters

Never average, minimize, or maximize a raw cumulative counter.

- Use window `increase` when the question asks how many events occurred.
- Use a derived `rate` when the question asks frequency or throughput.
- Apply minimum, average, and maximum only to a valid derived rate series.
- State the rate lookback or evaluation step when it materially affects the
  interpretation.

If metric type cannot be established from metadata, an inspected dashboard, or
a strong documented convention, report the uncertainty instead of silently
treating the series as a gauge.

### Histograms And Summaries

Prefer count, rate, sum, and source-supported quantiles. Preserve the
quantile's meaning and unit. Report minimum or maximum only when supported by
raw observations or a valid backend operator; do not infer them from buckets
that cannot establish the value.

Backend `topk` or `bottomk` must rank the requested full-window statistic, not
an arbitrary last range-query sample.

## Logs

For ordinary text logs, default to:

- count and rate when meaningful;
- severity or level when available;
- exact first seen when a supported bounded method provides it; and
- exact last seen under the same condition.

Group and rank by the requested parsed field or proven label. Use minimum,
average, and maximum only after explicitly parsing and unwrapping a numeric
field such as duration or size. Never average log text, categorical severity,
status codes, or identifiers.

Do not retrieve or return raw log bodies merely to satisfy first/last fields.
When an exact timestamp method is unavailable within the query and execution
bounds, omit those fields and state the limitation. A timestamp-only internal
lookup, when supported, does not authorize returning log examples.

Return sanitized examples only when explicitly requested, subject to the
20-line cap.

## Traces

For trace or span summaries, default to:

- count;
- error count and error rate when status is available;
- minimum, average, and maximum duration per requested group when an exact
  full-window aggregation or sufficient recomposable values are available; and
- p95 duration when latency or duration is the requested field and the
  datasource supports a full-window quantile with its approximation semantics
  disclosed.

Do not label an approximate quantile as exact.

Group only by proven resource/span attributes. Prefer trace-level intrinsics
for trace-level questions. Do not calculate numeric statistics over
categorical attributes merely because the query language accepts them.

TraceQL over-time functions aggregate per query step. Never average bucket
averages or quantiles, relabel per-step statistics as full-window statistics,
or derive full-window duration statistics from the bounded 20-summary fallback
subset. If the selected tool cannot produce an exact or explicitly supported
full-window result, omit the statistic and state the limitation.

## Dashboards And Datasource Discovery

Use inventory tables for dashboards, datasources, panels, query capabilities,
and proven scope dimensions. Return them without fabricated statistics: do not
add minimum, average, or maximum columns when there is no meaningful numeric
field.

## Partial And Ranked Results

Use an exact global ranking when it fits the query and execution bounds. When
it does not:

- partition and recombine only if the result remains exact;
- otherwise label the table as the top 20 of the returned bounded subset; and
- never call a bounded subset the global top 20.

For partial decomposition, list successful and failed branches in coverage,
retain full attribution, and do not fill failed branches with zero.

## Compact Template

Use this structure and replace only the fields applicable to the signal:

```markdown
## Outcome

<direct answer and complete/partial/no-data status>

## Query scope

| Item | Value |
| --- | --- |
| Signal and requested field | `<SIGNAL>` / `<FIELD>` |
| Absolute UTC window | `<START>` to `<END>` |
| Scope | `<ATTRIBUTED_SCOPE>` |
| Grouping and ordering | `<GROUP>`; `<DIRECTION>` on `<FIELD>` |

## Results

Displayed `<N>` of `<TOTAL>` matched groups; `<GLOBAL_OR_SUBSET>` ranking.

| <scope columns> | <group> | <requested field> | <signal statistics> |
| --- | --- | --- | --- |
| ... | ... | ... | ... |

## Coverage and access

| Status | Count | Notes |
| --- | ---: | --- |
| Queried | ... | ... |
| No data / denied / skipped / failed / truncated | ... | ... |

## Method and limitations

- Datasource and type: `<UID>` / `<TYPE>`
- Query or path category: `<SANITIZED_QUERY_OR_CATEGORY>`
- Native, fallback, or decomposition: `<METHOD>`
- Limits, sanitization, and remaining uncertainty: `<NOTES>`
```

Remove unused rows or columns instead of filling the report with `N/A`.

## Official Semantics

- Prometheus metric types and counter handling:
  <https://prometheus.io/docs/concepts/metric_types/> and
  <https://prometheus.io/docs/practices/instrumentation/>
- Prometheus over-time and ranking operators:
  <https://prometheus.io/docs/prometheus/latest/querying/functions/> and
  <https://prometheus.io/docs/prometheus/latest/querying/operators/>
- Loki numeric unwrapping and aggregation:
  <https://grafana.com/docs/loki/latest/query/troubleshoot-query/> and
  <https://grafana.com/docs/loki/latest/query/query_examples/>
- TraceQL metrics functions:
  <https://grafana.com/docs/tempo/latest/metrics-from-traces/metrics-queries/functions/>
