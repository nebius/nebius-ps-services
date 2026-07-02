# Audit Log Querying

This reference keeps the current Nebius Audit Logs query contract in one place
so `SKILL.md` can stay concise.

## Official Sources Checked

- Audit Logs overview: <https://docs.nebius.com/audit-logs>
- Viewing events: <https://docs.nebius.com/audit-logs/events/view>
- Filtering events: <https://docs.nebius.com/audit-logs/events/filter>
- Event structure and fields:
  <https://docs.nebius.com/audit-logs/events/reference>
- CLI list command:
  <https://docs.nebius.com/cli/reference/audit/v2/audit-event/list>
- IAM whoami: <https://docs.nebius.com/cli/reference/iam/whoami>
- CLI config get: <https://docs.nebius.com/cli/reference/config/get>
- Project get: <https://docs.nebius.com/cli/reference/iam/v2/project/get>
- Regions: <https://docs.nebius.com/overview/regions>

The local CLI was checked at version `0.12.239`, and
`nebius audit v2 audit-event list --help` includes `--region`.

## Command Contract

Use the read-only list command:

```bash
nebius audit v2 audit-event list \
  --parent-id <tenant_id> \
  --start <start_iso_8601_utc> \
  --end <end_iso_8601_utc> \
  --event-type control_plane \
  --region <region> \
  --filter "<filter>" \
  --page-size <n> \
  --format json
```

Required command inputs:

- `--parent-id`: tenant ID.
- `--start`: ISO 8601 timestamp.
- `--end`: ISO 8601 timestamp.

Useful query controls:

- `--event-type`: `control_plane` or `data_plane`; this skill uses
  `control_plane` only in v1.
- `--filter`: Nebius Audit Logs filter expression.
- `--page-size`: bounded result size.
- `--page-token`: page continuation token.
- `--all`: list every page; use only when explicitly requested.
- `--region`: region to retrieve logs from. The Nebius CLI documents
  `eu-north1` as the command default and notes a transition period until
  `13-08-2026` where events are written both to `eu-north1` and their origin
  region. After that date, events are stored only in their origin region and
  the region field becomes required.

## Default Resolution

Tenant:

1. Use explicit `--tenant-id`.
2. Else run `nebius config get tenant-id`.
3. Else stop and ask for a tenant ID or configured CLI profile.

Region:

1. Use explicit `--region`.
2. Else use the current/default project region when it can be discovered from
   an explicit `--project-id` or `nebius config get parent-id`.
3. Else try CLI config region keys if the local CLI exposes them.
4. Else use `eu-north1`.

Time range:

1. Use explicit `--start` and `--end` when supplied.
2. If only `--end` is supplied, use `--hours` before that end.
3. If only `--start` is supplied, end at current UTC time.
4. If neither is supplied, use the last 24 hours in UTC.

## Filter Contract

Nebius Audit Logs support `=`, `!=`, `:`, and `regex`, and combine filters with
`AND`. This skill composes only equality filters unless the user provides
`--raw-filter`.

Core filter fields used by this skill:

- `resource.metadata.id`
- `resource.metadata.type`
- `authentication.subject.tenant_user_id`
- `authentication.subject.service_account_id`
- `action`
- `service.name`
- `status`

Resource query:

```text
resource.metadata.id='<resource_id>'
```

Missing-resource fallback for the current logged-in principal:

```text
authentication.subject.tenant_user_id='<tenant_user_id>'
```

or:

```text
authentication.subject.service_account_id='<service_account_id>'
```

Optional filters are appended with `AND`, for example:

```text
resource.metadata.id='computeinstance-abc' AND action='DELETE' AND service.name='COMPUTE'
```

## Sanitization

Default output must avoid:

- tokens and token-derived fields
- credentials and static-key material
- raw request parameters and response payloads
- full raw event JSON
- subject names, user-controlled resource names, or email-like PII

The helper therefore parses JSON output and returns a reduced event summary.
Use `--raw` only when the user explicitly needs raw Nebius CLI output and
accepts the security risk. Use `--include-pii` only when subject names or other
PII-bearing summary fields are explicitly required.

## Live Validation

Prefer these validation levels:

1. Unit tests with the fake `nebius` executable.
2. `--dry-run` against the target profile, tenant, and region.
3. A live query only after the user opts in, with explicit tenant/profile and a
   low `--page-size`.

Do not run exports, updates, credential creation, IAM mutation, or CLI update
commands from this skill.
