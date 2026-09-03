# Nebius Audit Log

`nebius-audit-log` is an explicit-only Codex skill for bounded, read-only
Nebius Control Plane Audit Logs queries. It complements the broader `nebius`
skill by owning only Audit Logs inspection.

## Default Behavior

- Time range: last 24 hours in UTC.
- Event type: `control_plane`.
- Region: explicit input, then discoverable project region, then `eu-north1`.
- Event filters: no action, service, resource type, or status filter unless
  requested.
- Resource filter: `resource.metadata.id='<resource_id>'` when provided.
- Missing resource fallback: current authenticated Nebius subject ID from
  `nebius iam whoami --format json`.
- Output: sanitized summary by default, sanitized JSON when requested, raw
  Nebius CLI output only with `--raw`.

## Helper Script

Run a dry-run first when the target is unclear:

```bash
python3 nebius-audit-log/scripts/query_audit_logs.py \
  --resource-id <resource_id> \
  --tenant-id <tenant_id> \
  --region <region> \
  --hours 24 \
  --dry-run
```

Run the bounded query:

```bash
python3 nebius-audit-log/scripts/query_audit_logs.py \
  --resource-id <resource_id> \
  --tenant-id <tenant_id> \
  --region <region> \
  --hours 24 \
  --page-size 100
```

When no resource ID is supplied, the helper resolves the current principal and
queries by `authentication.subject.tenant_user_id` or
`authentication.subject.service_account_id`.

## Safety Rules

- The skill never creates credentials, mutates IAM, updates the Nebius CLI, or
  starts Audit Logs exports.
- The helper uses subprocess argument arrays, not shell command construction.
- Raw event payloads are not printed unless `--raw` is explicitly passed.
- Subject names, user-controlled resource names, and other PII-bearing summary
  fields require `--include-pii`.
- Live validation is opt-in and should use a low `--page-size`.

## Files

- `SKILL.md`: runtime routing, workflow, guardrails, and validation contract.
- `agents/openai.yaml`: OpenAI metadata with explicit-only invocation policy.
- `references/audit-log-querying.md`: official Nebius command and field notes.
- `scripts/query_audit_logs.py`: read-only query helper.
- `scripts/test_query_audit_logs.py`: unit tests using a fake `nebius` CLI.
- `evals/trigger-prompts.csv`: canonical should-trigger and should-not-trigger examples.
- `evals/process-cases.md`: supplemental query workflow cases.

## Validation

```bash
python3 -B nebius-audit-log/scripts/test_query_audit_logs.py
python3 <path-to-skill-creator-quick-validator> nebius-audit-log
python3 <path-to-align-skill-structure-validator> nebius-audit-log
git diff --check
```
