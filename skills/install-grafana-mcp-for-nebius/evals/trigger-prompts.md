# Grafana MCP Installer Trigger Evals

| Prompt or state | Expected behavior |
| --- | --- |
| `$install-grafana-mcp-for-nebius` install and configure Grafana MCP | Inspect existing state and explicitly converge only missing approved setup. |
| `$install-grafana-mcp-for-nebius` check my existing Codex registration | Run read-only setup/config inspection and report drift. |
| `$install-grafana-mcp-for-nebius` repair an authentication or wrapper failure | Diagnose the setup path and apply only explicitly authorized repair. |
| `$install-grafana-mcp-for-nebius` configure external Grafana for Nebius read endpoints | Use the guarded external-Grafana IAM/static-key/datasource flow. |
| Setup completes and datasources list successfully | Report readiness and hand routine queries to `$nebius-grafana-query`. |
| Show GPU utilization for one existing cluster | Do not use the installer for the outcome query; route to `$nebius-grafana-query`. |
| Summarize recent Loki errors for one resource | Route to `$nebius-grafana-query`; do not rerun setup. |
| Inspect dashboard panel queries | Route to `$nebius-grafana-query`; do not mutate config. |
| User asks naturally for metrics without naming the installer | Do not invoke setup implicitly. |
