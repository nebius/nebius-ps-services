# Nebius Grafana Query Trigger Evals

| Prompt or state | Expected behavior |
| --- | --- |
| Show GPU utilization for MK8s cluster `<cluster>` in project `<project>` over the last day | Select the query skill, discover the datasource/metric/labels, and return a bounded per-node summary. |
| List monitoring and logging datasources available for project `<project>` | Select the query skill and perform read-only datasource discovery. |
| Check CPU and memory for resource `<resource>` without a time range | Default to the last hour and return a compact result. |
| Count GPU XID errors by node and GPU for the last seven days, without naming a project or resource | Ask for authoritative project/resource scope before running a bounded aggregate query. |
| Summarize recent error logs for one project/resource | Return counts by default; include at most 20 sanitized examples only when explicitly requested. |
| Inspect the queries used by dashboard `<dashboard>` | Use read-only dashboard and panel-query tools without changing the dashboard. |
| Can this Grafana MCP configuration query traces? | Inspect datasources and exposed proxied tools; do not promise unavailable `tempo_*` tools. |
| Grafana tools are missing or authentication fails | Stop and tell the user to invoke `$install-grafana-mcp-for-nebius` explicitly. |
| Install Grafana MCP and register it with Codex | Do not select the query workflow; use explicit `$install-grafana-mcp-for-nebius`. |
| Refresh the Nebius Grafana token or repair MCP config | Do not repair implicitly; route to the explicit installer. |
| Configure external Grafana with a Nebius static key | Use the explicit installer workflow, not the query skill. |
| Query Nebius Control Plane Audit Logs | Use `$nebius-audit-log`, not Grafana. |
| Create a dashboard, alert, incident, annotation, or snapshot | Refuse to widen the read-only skill and require an explicit write-capable workflow. |
