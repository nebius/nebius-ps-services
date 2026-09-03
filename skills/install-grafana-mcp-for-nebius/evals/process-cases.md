# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Fresh routing checks use CSV rows `grafana-install-positive-01` through
`grafana-install-positive-05` and `grafana-install-negative-01` through
`grafana-install-negative-06`. The table below assumes the skill has already
been selected and preserves only setup-state and handoff expectations.

| Prompt or state | Expected behavior |
| --- | --- |
| `$install-grafana-mcp-for-nebius` install and configure Grafana MCP | Inspect existing state and explicitly converge only missing approved setup. |
| `$install-grafana-mcp-for-nebius` check my existing Codex registration | Run read-only setup/config inspection and report drift. |
| `$install-grafana-mcp-for-nebius` use my active human CLI profile for managed Grafana | Validate `.user_profile.id`, pin the active profile, and create profile-scoped private state only after explicit apply approval. |
| `$install-grafana-mcp-for-nebius` switch from the old shared token registration to profile `<human_profile>` | Show the exact migration; replace the named MCP entry only with explicit `--apply --replace-existing`. |
| `$install-grafana-mcp-for-nebius` repair an authentication or wrapper failure | Diagnose the setup path and apply only explicitly authorized repair. |
| Ambient profile is `codex-agent-<project>` while a human profile is pinned | Clear agent selectors and credentials; validate and mint only through the pinned human profile. |
| The pinned profile now resolves to another user or a service account | Fail closed before minting or starting MCP; do not rewrite the binding automatically. |
| The installed MCP build lacks token-file support | Fail closed and require an updated `mcp-grafana`; do not add an inline-token compatibility path. |
| The cached startup token is missing or older than one hour | Renew through the pinned human profile before MCP launch, permit browser authentication only for the foreground identity check, mint and revalidate noninteractively, wait long enough to reuse one concurrent foreground renewal, and fail closed without launching MCP if renewal fails. |
| The Codex MCP entry lacks the canonical startup timeout | Set only `startup_timeout_sec = 300.0` in place when every other structured value is canonical and the digest-bound config bytes remain unchanged; do not use inline `sh -c` or put a token in TOML. |
| A parent inspection command combines direct Nebius CLI use with `env -u` or `unset` of managed auth variables | Treat it as expected command-shape enforcement: make no setup mutation, run profile discovery separately, and invoke the setup helper directly. |
| `$install-grafana-mcp-for-nebius` configure external Grafana for Nebius read endpoints | Use the guarded external-Grafana IAM/static-key/datasource flow. |
| Setup completes and datasources list successfully | Report readiness and hand routine queries to `$nebius-grafana-query`. |
| Show GPU utilization for one existing cluster | Do not use the installer for the outcome query; route to `$nebius-grafana-query`. |
| Summarize recent Loki errors for one resource | Route to `$nebius-grafana-query`; do not rerun setup. |
| Inspect dashboard panel queries | Route to `$nebius-grafana-query`; do not mutate config. |
| User asks naturally for metrics without naming the installer | Do not invoke setup implicitly. |
