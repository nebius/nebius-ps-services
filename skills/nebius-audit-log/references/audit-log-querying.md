# Audit Log Querying

## Authoritative contracts

Reviewed against current official documentation and public API schemas on
2026-09-11; no live tenant query or local CLI authentication was performed.

- [Viewing events and tenant roles](https://docs.nebius.com/audit-logs/events/view)
- [Supported services](https://docs.nebius.com/audit-logs/services)
- [Filtering events](https://docs.nebius.com/audit-logs/events/filter)
- [Event fields and operation status](https://docs.nebius.com/audit-logs/events/reference)
- [Audit list CLI](https://docs.nebius.com/cli/reference/audit/v2/audit-event/list)
- [Selected CLI profile](https://docs.nebius.com/cli/reference/profile/current)
- [Whoami](https://docs.nebius.com/cli/reference/iam/whoami)
- [CLI exit codes](https://docs.nebius.com/cli/exit-codes)
- [Profile response schema](https://github.com/nebius/api/blob/main/nebius/iam/v1/profile_service.proto)
- [Project schema](https://github.com/nebius/api/blob/main/nebius/iam/v2/project.proto)
- [Audit list schema and page limit](https://github.com/nebius/api/blob/main/nebius/audit/v2/audit_event_service.proto)

## Identity is separate from authorization

`profile current` resolves the profile selected by CLI configuration,
`NEBIUS_PROFILE` or an explicit `--profile`. Keep that selected profile and the
inherited environment consistent for all calls. The CLI owns authentication,
including renewable credentials; the helper must not read keys or fetch tokens.

Whoami returns one documented variant. For `user_profile`, select exactly one
`tenants` entry whose `tenant_id` equals the query tenant, then use its
`tenant_user_account_id`. For `service_account_profile`, use
`info.metadata.id`. Anonymous, absent, invalid or ambiguous identities stop.
Do not recursively search unrelated fields for an ID with a matching prefix.

The first valid bounded audit-list response proves effective read access for
that request and becomes page one. Every subsequent page remains subject to
server authorization. Tenant `auditlogs.audit-event-viewer` is sufficient;
`admin` also works. Do not enumerate roles or require broad IAM-read access as a
precondition. A failed identity lookup is not an audit-permission verdict.

## Scope and region

The audit list parent is always a tenant ID. The helper requires a resource,
subject, current-subject or tenant-wide selector. Additional filters narrow
that explicit scope; there is no hidden actor selection. A tenant-wide query
still covers only the chosen region and window.

Use explicit region whenever known. Otherwise verify the explicit/configured
project's metadata ID and tenant parent, then read `spec.region`. Failed or
missing discovery stops. The API documents that after 2026-08-13 events are
stored only in their origin region; an arbitrary `eu-north1` fallback can miss
an event. Project lookup is not needed when region is supplied explicitly.

## Query and response contract

Use `nebius audit v2 audit-event list` with tenant parent, absolute start/end,
region, `control_plane`, optional filter, bounded page size and JSON output.
Maximum page size is 500. The helper owns pagination instead of CLI `--all`.

The canonical envelope uses `items` and `next_page_token`. Protobuf default
omission permits `{}` or a token-only page; these differ from empty stdout,
invalid JSON, wrong types, duplicate keys or an alias-only envelope. Validate
all events in a page before committing it to the result. Do not translate a
provider/parser failure into an empty event collection.

`DONE`, `STARTED` and `ERROR` describe the operation, and event authorization
describes the historical actor. Neither proves current reader access. In MK8s
investigations, inspect `service.name='MK8S'` and `action='DELETE'` together
with resource ID, time and status. Preserve service-account attribution without
inventing the initiating human; resolving that may require separately scoped
CI or automation evidence.

## Filter and privacy boundary

The helper accepts only documented noncredential fields and AND-connected
comparisons or regex predicates. Quoted literals are parsed as strings, so the
word OR inside a quoted pattern is not an OR operator. Unsupported syntax and
credential fields fail before CLI access. Structured selectors cannot be
replaced or broadened by an extra expression.

All reports use an allowlist: safe scope, IDs, service/action/status/time,
source method, request ID, response code and optional names. Never serialize
whole identity resources or audit payloads for convenience. Sensitive filter
literals and provider stderr are excluded even in previews/errors. Raw payload
passthrough is not supported.

## Failure and evidence rules

CLI code 7 is authentication failure; 15/55 are permission denial; 12/52 are
deadlines; 20/60 are unavailable service; 4 is configuration failure. Preserve
the failing stage and classify other exit codes without guessing from text.
Do not fix credentials or grant permissions to make the query succeed.

Deadlines, output limits, page limits and later errors yield partial evidence
with earlier validated pages retained. A returned token can resume only the
same query with the original absolute window. Cycles have no safe continuation.
No events in a failed or partial report is never evidence of absence.

`--dry-run` makes zero CLI calls and never establishes authentication or access.
Offline fake-CLI tests, source-copy portability, installed runtime loading and
live tenant access are distinct validation levels. Only an explicitly scoped
live request can supply the last one.
