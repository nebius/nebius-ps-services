# Nebius Grafana Query

This skill answers read-only Nebius observability questions through an
already-configured `grafana-nebius` Grafana MCP server. It is implicitly
invokable, so natural-language metrics, logs, datasource, dashboard-query, and
trace-availability questions can select it without an explicit skill mention.

## Boundary

The names describe different layers:

- `$nebius-grafana-query` is the Codex skill that defines the safe query
  workflow.
- `grafana-nebius` is the MCP server entry that exposes Grafana tools.

The skill never installs or configures the MCP server, repairs authentication,
changes IAM, issues static keys, or mutates Grafana. Missing server,
configuration, or authentication belongs to the explicit
`$install-grafana-mcp-for-nebius` workflow.

## Workflow

The skill lists available datasources, discovers metric and log labels, uses
dashboard panel queries when useful, and runs bounded PromQL or LogQL against
the returned datasource UID. It defaults an omitted time range to one hour,
requires enough project/resource scope to avoid cross-project queries, and
summarizes results instead of dumping raw samples.

Logs default to counts and summaries. Raw log lines require an explicit request
and are limited to 20 sanitized examples. Trace querying is reported as
available only when the configured MCP server exposes the required proxied
tools.

## Examples

```text
Show CPU and memory usage for the nodes in MK8s cluster <MK8S_CLUSTER_ID> in
project <PROJECT_ID> over the last 24 hours. Summarize by node and call out
missing data.
```

```text
Check whether MK8s cluster <MK8S_CLUSTER_ID> in project <PROJECT_ID> had any
GPU XID errors in the last 7 days. Return counts by node and GPU only.
```

```text
Summarize recent error logs for resource <RESOURCE_ID> in project <PROJECT_ID>
over the last hour. Return counts and the most relevant short examples only.
```

## MCP Flow

```text
User prompt
  -> Codex selects nebius-grafana-query
  -> the skill chooses a read-only grafana-nebius tool
  -> grafana-nebius calls Grafana and its datasource APIs
  -> Codex returns a bounded, sanitized summary
```

## Files

- `SKILL.md`: trigger, runtime boundary, workflow, guardrails, and output.
- `agents/openai.yaml`: implicit invocation and UI metadata.
- `references/query-guide.md`: detailed discovery and query guidance.
- `evals/trigger-prompts.md`: activation and routing examples.
