---
name: nebius-audit-log
description: "Use only when the user explicitly asks to query Nebius Control Plane Audit Logs for a resource, subject, service, action, status, region, or time range. Requires explicit invocation because it reads tenant-level audit/security data; do not use for broad Nebius SDK/IAM/VPC/quota/MK8s/observability automation."
---

# Nebius Audit Log

Query Nebius Control Plane Audit Logs with the Nebius CLI while keeping the
operation read-only, bounded, and sanitized by default.

## Invocation Policy

This skill requires explicit invocation. Do not implicitly invoke it for general
Nebius questions, broad cloud automation, or routine debugging because Audit
Logs are tenant-level security data.

Use `$nebius-audit-log` only after the user explicitly asks to inspect Nebius
Audit Logs.

## Use This Skill For

- Finding Control Plane Audit Logs for a specific Nebius resource ID.
- Checking what the current authenticated Nebius principal did when no resource
  ID is provided.
- Narrowing Audit Logs by region, time window, action, service, resource type,
  status, or an explicit raw Nebius filter.
- Producing a dry-run command for a bounded read-only Audit Logs query.

## Non-Goals

- Do not create, rotate, persist, or repair Nebius credentials.
- Do not mutate IAM, projects, tenants, resources, buckets, exports, or CLI
  profiles.
- Do not run Audit Logs export-to-bucket workflows in v1 of this skill.
- Do not use this skill for general Nebius SDK, IAM bootstrap, object storage,
  VPC, quota, MK8s, GPU, or observability work; use `nebius` for those.
- Do not print raw audit event payloads, request or response payloads, tokens,
  credentials, subject names, or PII-bearing fields unless the user explicitly
  requests the required flags.

## Inputs

Accept any of these inputs from the user:

- Resource ID: preferred; maps to `resource.metadata.id='<resource_id>'`.
- Tenant ID: explicit `--tenant-id` or CLI config `tenant-id`.
- Project ID: optional helper input for region discovery.
- Region: explicit `--region`, discoverable project region, or fallback
  `eu-north1`.
- Time range: `--start` and `--end`, or `--hours` for the trailing window.
- Optional filters: action, service, resource type, status, or raw Nebius
  filter text.
- Output mode: sanitized summary or sanitized JSON by default; raw output only
  when explicitly requested.

## Required Reads

Before changing this skill or answering detailed usage questions, read
`references/audit-log-querying.md`. It records the current official Nebius CLI
and Audit Logs field contract, default resolution behavior, and safety rules.

## Workflow

1. Confirm the request is explicitly about Nebius Audit Logs.
2. Prefer the bundled helper for repeatable queries:

   ```bash
   python3 nebius-audit-log/scripts/query_audit_logs.py \
     --resource-id <resource_id> \
     --tenant-id <tenant_id> \
     --region <region> \
     --hours 24
   ```

3. Resolve defaults:
   - Tenant: explicit `--tenant-id`, then `nebius config get tenant-id`, else
     stop with guidance.
   - Region: explicit `--region`, then current/default project region when
     discoverable, then `eu-north1`.
   - Time range: last 24 hours in UTC unless the user provides start/end or a
     different hour window.
   - Event type: always `control_plane` in v1.
   - Event filters: do not add action, service, resource type, or status
     filters unless requested.
4. If the user omits a resource ID, query the current authenticated principal
   instead:
   - `authentication.subject.tenant_user_id='<id>'` for tenant-user principals.
   - `authentication.subject.service_account_id='<id>'` for service accounts.
5. Keep pagination bounded by default. Use `--page-size` or `--all` only when
   the user has asked for broader results and the output remains safe.
6. Use `--dry-run` before live queries when the target tenant, profile, region,
   or filter needs confirmation.

## Guardrails

- This skill is read-only. Do not run Nebius commands that create, update,
  delete, export, or persist cloud resources.
- Never run `nebius update` as part of this skill.
- Never print tokens, credentials, private keys, full raw event payloads,
  request payloads, response payloads, subject names, or user-controlled
  resource names by default.
- Require `--raw` before passing through raw Nebius CLI output.
- Require `--include-pii` before showing PII-bearing summary fields such as
  `authentication.subject.name`.
- Treat `--raw-filter` as user-owned advanced input. Do not synthesize filters
  on token credential fields unless explicitly asked.
- Prefer official Nebius docs and local CLI help over memory for command flags,
  filter fields, event structure, and region behavior.
- Live validation must be opt-in, bounded by a low page size, and avoid raw
  payload output.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Validation

Run these checks after edits:

```bash
python3 -B nebius-audit-log/scripts/test_query_audit_logs.py
python3 <path-to-skill-creator-quick-validator> nebius-audit-log
python3 <path-to-align-skill-structure-validator> nebius-audit-log
git diff --check
```

If available, also run:

```bash
markdownlint nebius-audit-log/SKILL.md nebius-audit-log/README.md README.md CHANGELOG.md
```

## Output Contract

Report:

- resolved tenant source without exposing credentials
- resolved region and time window
- filter class used, such as resource ID or current subject
- whether output was sanitized or raw
- validation run, live validation skipped or run, and any remaining unverified
  behavior

## References

- `references/audit-log-querying.md`
- `scripts/query_audit_logs.py`
- `scripts/test_query_audit_logs.py`
- `evals/trigger-prompts.md`
