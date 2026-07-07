# Trigger Prompts

Use these examples when checking whether the skill activation boundary is clear.

## Should Trigger

- `$nebius-audit-log Show Control Plane Audit Logs for computeinstance-abc in eu-west1 for the last 6 hours.`
- `$nebius-audit-log Query audit events for serviceaccount-abc yesterday, sanitized output only.`
- `$nebius-audit-log Build a dry-run command for Audit Logs on tenant-abc and resource mk8scluster-abc.`

## Should Not Trigger

- `Use Nebius SDK to create a service account and access key.`
- `Check MK8s GPU quota in all Nebius regions.`
- `Install or update the Nebius CLI.`
- `Set up Grafana MCP for Nebius observability.`
