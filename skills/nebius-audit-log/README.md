# Nebius Audit Log

An explicit-only skill for investigating Nebius Control Plane Audit Logs.
The Python 3.10+ helper uses the existing Nebius CLI configuration, verifies the
caller and effective tenant audit-read access, and returns sanitized results.
It does not install software, repair credentials, change IAM or export logs.

## Investigate a deleted MK8s cluster

Choose the cluster ID, tenant, origin region and relevant time window:

```bash
python3 nebius-audit-log/scripts/query_audit_logs.py \
  --resource-id <cluster_id> --tenant-id <tenant_id> \
  --region <origin_region> --service MK8S --action DELETE \
  --start 2026-09-10T00:00:00Z --end 2026-09-11T00:00:00Z
```

Inspect event status: `DONE`, `STARTED` and `ERROR` have different meanings.
Actor IDs identify users or service accounts; an automation account does not
identify a human. Add `--include-pii` only when names are explicitly needed.
If the cluster ID is unknown, an explicitly authorized broader investigation
can use `--tenant-wide` with service, action, region and time filters. It never
adds the investigating caller as an implicit actor filter.

## Credentials and permission checks

`--profile` overrides the configured profile. Otherwise `nebius profile current`
resolves the CLI-selected profile, including its documented environment
selection. The helper uses that profile and an environment snapshot for all
calls. It does not parse keys or fetch tokens. Keep the selected CLI
configuration unchanged during an investigation.

Tenant comes from `--tenant-id` or that profile's `tenant-id`. Every live query
runs `whoami` first, selecting the user's tenant-account mapping for that tenant
or the service-account identity. The first valid audit page proves effective
read access and is retained as page one. No separate tenant-wide probe or IAM
grant enumeration is performed. Access is checked by the server on every page;
a later denial is reported with earlier pages retained as partial evidence.

The documented tenant roles are `auditlogs.audit-event-viewer` or `admin`;
project access, `whoami` and successful previews alone do not prove this
permission. [Nebius prerequisites](https://docs.nebius.com/audit-logs/events/view)

Region is explicit, otherwise discovered from `--project-id` or configured
`parent-id`. Discovery verifies project ID, tenant parent and `spec.region`.
It stops on missing data or denied access. `--project-id` is only a discovery
input, not a project event filter. There is no guessed region fallback.

## Helper interface

```text
python3 scripts/query_audit_logs.py <selector> [options]
```

Exactly one selector is required:

| Selector | Meaning |
| --- | --- |
| `--resource-id ID` | Events for one resource |
| `--subject-id ID` | Events by a tenant-user or service-account ID |
| `--current-subject` | Events by the verified caller |
| `--tenant-wide` | All actors/resources within selected tenant, region and window |

| Option | Meaning and default |
| --- | --- |
| `-h`, `--help` | Print help without cloud access |
| `--tenant-id ID` | Explicit tenant; otherwise selected CLI configuration |
| `--project-id ID` | Project used only for region discovery |
| `--region REGION` | Explicit origin region; otherwise verified project discovery |
| `--profile NAME` | Explicit CLI profile; otherwise configured selection |
| `--start TIMESTAMP` | ISO 8601 start; otherwise end minus hours |
| `--end TIMESTAMP` | ISO 8601 end; otherwise current UTC time |
| `--hours N` | Positive finite trailing hours; default 24 |
| `--action VALUE` | One action, such as DELETE; repeated values are rejected |
| `--service VALUE` | Service filter, such as MK8S |
| `--resource-type VALUE` | Resource type filter |
| `--status VALUE` | STARTED, DONE or ERROR filter |
| `--raw-filter EXPR` | Additional validated AND-connected noncredential predicates |
| `--page-size N` | Items per page, 1..500; default 100 |
| `--max-pages N` | Maximum pages, 1..100; default 1 |
| `--page-token TOKEN` | Resume the same query with explicit start/end and no hours |
| `--timeout SECONDS` | Overall budget, positive and at most 600; default 120 |
| `--dry-run` | Offline scope preview; no CLI calls or access verification |
| `--format FORMAT` | Sanitized summary or JSON; default summary |
| `--include-pii` | Include names in event summaries |

Timestamps without offsets use UTC. Every invocation freezes its absolute
window. `--raw-filter` accepts comparisons (`=`, `!=`, `:`) with quoted strings
and `regex(field, 'pattern')`, connected by uppercase `AND`. OR, comments,
grouping outside regex calls, credential fields and multiline expressions are
rejected before CLI access. Values are never echoed in reports or errors.

```bash
python3 nebius-audit-log/scripts/query_audit_logs.py \
  --current-subject --dry-run --format json
```

A dry run can leave configured tenant, region and caller unresolved. It always
reports access `not_checked`, `complete: false` and mode `dry_run`. It does not
export an executable command or expose raw filter literals.

## Results, limits and continuation

JSON returns `mode`, `scope`, `caller`, `access`, `events`, `complete`, `errors`
and `next_page_token`. Scope includes safe selectors, structured filters,
absolute timestamps, profile-selection source and extra predicate field/operator
metadata; it omits profile names and raw predicate values. Summary output
communicates the same scope, status and errors. Events include actor/resource
IDs, time, action, operation status, source method, request ID and response code.
`event_authorized` concerns the historical actor, not the current reader.

Access starts `not_checked`, becomes `verified` after a validated page, and is
`denied` on an audit permission denial. `verified_pages` preserves how many
pages succeeded. Other failures carry their exact stage/code; earlier verified
pages are not a promise of continued access. A complete query with zero
collected events means no matching events within its scope, not global absence.

Each CLI subprocess gets at most 30 seconds, one attempt, no interactive input
or browser opening, and an 8 MiB combined stdout/stderr limit. All calls share
the overall deadline and a 32 MiB output budget. Exceeded bounds terminate and
reap the CLI. Raw output is never written to files by the helper.

A remaining token at the page limit produces partial results. Later errors
retain already validated pages and the pending token. Cyclic tokens stop with
no continuation. To resume, keep the same profile, tenant, region, selector
and filters, and copy the report's absolute timestamps:

```bash
python3 nebius-audit-log/scripts/query_audit_logs.py \
  --resource-id <cluster_id> --tenant-id <tenant_id> --region <origin_region> \
  --service MK8S --action DELETE \
  --start <original_start> --end <original_end> --page-token <returned_token>
```

A page token does not encode or independently verify the original query in this
helper; the caller must retain the other original parameters. Increase
`--max-pages` only when the user requests broader collection in the same scope.

Exit codes: `0` means a complete query or valid offline preview; `1` means live
failure or partial coverage; `2` means invalid arguments. Errors use safe stage,
code and message fields, never provider stderr. Authentication, permission,
configuration, unavailable service, invalid responses and exhausted bounds
remain distinct. Invalid arguments produce a safe JSON diagnostic on stderr
before a query report exists, including when output-format parsing fails.

## Interface changes

The implicit current-subject and `eu-north1` fallbacks are removed. Choose a
selector explicitly and supply or discover the correct region. `--all` is
replaced by bounded `--max-pages`. `--raw` and raw-only YAML/table/text output
are removed; summary and JSON are always sanitized. No compatibility aliases
are provided. Use full documented flag names; abbreviations are rejected.
Preview output is safe scope metadata, not a full CLI command.

## Validation

From the skills project root:

```bash
python3 -B nebius-audit-log/scripts/test_query_audit_logs.py
python3 -B align-skill/scripts/validate-skill-structure.py --require-evals nebius-audit-log
ruff check --no-cache nebius-audit-log/scripts
ruff format --check nebius-audit-log/scripts
markdownlint nebius-audit-log/SKILL.md nebius-audit-log/README.md \
  nebius-audit-log/references/audit-log-querying.md nebius-audit-log/evals/process-cases.md
```

Tests bind a fake executable, use synthetic fixtures and require no cloud
credentials or network. The implementation is split into the query workflow,
bounded CLI transport and filter grammar in `scripts/`. Copied-source tests
prove portability only. Real installation, fresh runtime routing and bounded
live access tests are separate explicitly requested evidence lanes.
