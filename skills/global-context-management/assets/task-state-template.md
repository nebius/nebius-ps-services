# Current Codex task state

## Working scope

## Objective

## Constraints

## Plan

## Decisions

## Changed files

## Validation

## Risks

## Active remediation budget

When troubleshooting has initialized `codex-remediation-budget:v1`, preserve
its single bounded marker here exactly. Omit this section when no remediation
budget is active. Do not place raw errors, logs, secrets, private endpoints, or
customer data in the marker. If `troubleshoot` establishes a causally
independent blocker, replace the marker with that blocker's fresh attempt-1
budget and retain only a concise earlier outcome in prose.

## Next action

## Summary hygiene

Keep this file as a compact rolling summary, not an append-only transcript.
Replace stale or superseded details with the latest validated state. Do not
include raw logs, broad command output, full prompts, secrets, customer data,
private URLs, or copied documentation. If this file grows too large to scan
quickly or approaches 12 KiB, summarize it before continuing.
