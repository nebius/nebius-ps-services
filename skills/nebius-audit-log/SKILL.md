---
name: nebius-audit-log
description: "Use only when the user explicitly asks to investigate or query tenant-level Nebius Control Plane Audit Logs by resource, actor, service, action, status, region or time. Verifies caller and audit access; not for general cloud logs or auth repair."
disable-model-invocation: true
---

# Nebius Audit Log

## Help

For `$nebius-audit-log --help` or `$nebius-audit-log -h` (including native Claude forms), return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Agent Compatibility

Use `$nebius-audit-log` in Codex, `/nebius-audit-log` in Claude Code, or
`/skills:nebius-audit-log` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose and Invocation

Use `$nebius-audit-log <audit task>` only for an explicit audit investigation,
including who changed or deleted a cloud resource. No additional public flags
beyond `-h, --help`; the bundled query helper has its own `--help`.
General Nebius work belongs to `nebius`; ordinary Grafana logs belong to
`nebius-grafana-query`. Credential diagnosis belongs to
`agent-nebius-auth-diagnose`. This skill never repairs credentials or IAM.

## Required Reads

Read `references/audit-log-querying.md` before detailed usage or investigation.
Use `README.md` for the helper interface, examples and validation commands.

## Workflow

1. Establish the explicit audit request, target tenant, origin region, time
   window and intended investigation. Existing CLI credentials are allowed;
   preserve an explicitly selected profile. Ask only for material missing scope.
2. Select exactly one investigation scope: resource ID, explicit subject ID,
   current subject, or explicit tenant-wide search. Never replace an unknown
   actor with the investigating caller. Tenant-wide still means one region and
   one time window; do not broaden scope to discover more results silently.
3. Use the bundled deterministic helper:

   ```bash
   python3 nebius-audit-log/scripts/query_audit_logs.py \
     --resource-id <cluster_id> --tenant-id <tenant_id> \
     --region <origin_region> --hours 24 --service MK8S --action DELETE
   ```

4. The helper resolves one CLI profile and tenant, verifies the caller with
   tenant-aware `whoami`, and resolves the region explicitly or from a verified
   project in that tenant. A discovery error stops the query. No default region
   or credential substitution is permitted.
5. The first bounded audit page establishes effective read access and is kept
   as page one. A login, configured tenant, `whoami` or offline preview alone
   never proves audit permission. Tenant `auditlogs.audit-event-viewer` is the
   minimum documented role; `admin` also suffices. Do not enumerate grants or
   demand administrator access just to check a read.
6. Inspect the report's access status, errors and completeness before drawing
   conclusions. Preserve the returned scope/time window. Request more pages
   only within the user's intended scope; resume using the same parameters and
   explicit absolute start/end with the returned token.
7. Report the actor ID, action, operation status, time, resource and safe
   correlation evidence. Names require explicit `--include-pii`. Distinguish a
   completed deletion from a failed/started request. A service-account actor
   does not establish the human behind automation; historical event
   authorization describes that actor, not the audit reader.

## Offline Preview

`--dry-run` validates supplied inputs without executing any CLI command. It
shows safe scope and predicate metadata with raw filter values omitted.
Configured tenant/region and current-subject identity can remain unresolved;
authentication and audit access are always unverified. It is a preview, not an
executable command export or permission test.

## Guardrails and Failure Handling

- Read-only cloud requests only. Never create/rotate credentials, repair login,
  change profiles or IAM, update the CLI, export logs, or install this skill.
- Keep credentials with the configured CLI. Do not fetch or print access
  tokens, inspect credential files, or introduce a second authentication path.
- All output is sanitized summary or JSON. No raw payload mode is supported.
  Never print credential fields, request/response payloads, provider stderr,
  full commands, or sensitive filter literals, including in errors/previews.
- Keep names opt-in. Error text and unknown CLI failures remain safe and typed;
  do not infer permission denial by matching provider error strings.
- Default to 24 hours, 100 events and one page. The helper enforces time,
  output and pagination bounds even when broader results are explicitly asked
  for. Bounds and later-page failures produce partial evidence, not absence.
- Live validation requires an explicit request with scope and a small page
  size. Source tests and copied-source tests do not prove live access or fresh
  skill loading. Public documentation is evidence, not command authorization.

## Validation

Run the offline helper tests and strict skill checks described in `README.md`.
Keep source tests, isolated-copy portability, installed/runtime routing and
live audit access as separate evidence lanes.

## Output Contract

Report resolved scope, caller ID, effective audit-access outcome, event status
and correlation, completeness, safe errors, and continuation when available.
State unresolved inputs and unverified lanes. Do not conclude that no deletion
occurred from a failed, partial, wrong-region or narrowly scoped query.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
