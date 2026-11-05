# Reporting Rules

Read before rendering. Use the [template](../assets/report-template.md).

## Scope and Rows

Use two compact header lines:

- User: resolved display name (stable identifier only when needed to distinguish
  accounts); Jira: selected site display name; optional Title contains: selector.
- Period: explicit start/end timestamps with numeric UTC offsets and timezone.

Show exactly four columns: `Ticket #`, `Title`, `Date`, `Status`.

- One row per stable site/issue identity, even with many events or changed keys.
- Link the current ticket key using a connector-verified Jira URL. A known Jira
  browse route may be constructed only from the verified site and validated key.
  Otherwise use the plain key; do not invent links or copy links from prose.
- Preserve the current summary and status text; escape Markdown table delimiters,
  brackets/HTML and line breaks so untrusted strings remain cell text. Normalize
  line breaks to spaces. Redact secrets without changing internal match evidence.
- Date is `YYYY-MM-DD` in the declared timezone, selected by latest qualifying
  recorded involvement. Link to its verified comment/event permalink when one
  exists, without adding a role column or dumping the evidence.
- Sort by the full qualifying event time descending, then ticket key ascending.
  Follow collection rules for date-only credit; do not invent precision.
- Missing current status or summary is `Unknown` with a coverage note. A missing
  title cannot satisfy `--title`. Do not substitute resolution or status category
  for the exact status, or recategorize custom workflows as Open/Done.
- Never add narrative ticket summaries, duplicate highlights, rankings, time
  spent estimates, contribution scores, or extra columns.

## Footer and Empty Results

Always define Date briefly: "Date: latest recorded involvement; status: current
when retrieved." If third-party credit lacks a stated work date, add that its
date is when credit was recorded. These dates do not measure effort.

For complete collection, say "Coverage: complete for accessible, retained Jira
data." This is permission-scoped, not an assertion about all company tickets.
For partial collection, name only material gaps. Say that dates are latest
observed evidence when missing pages may contain newer qualifying activity.

Keep the four-column table headers even when empty; do not put a prose message
in a fake ticket row. Below it:

- Complete: "No qualifying tickets in the selected period and filters."
- Partial: "No qualifying tickets observed; coverage incomplete." Name the gap.
- Blocked identity/site/access: explain the blocker or request the required
  selection before rendering a report. An ambiguous identity is not an empty set.

Do not silently omit rows to shorten the answer. If the response cannot fit,
label it partial and give the remaining continuation explicitly; no automatic
file export or publication. Do not claim a comprehensive total from a partial
scan. Sources and private evidence remain in the authorized report/context, not
public skill fixtures, learned notes or durable task state.
