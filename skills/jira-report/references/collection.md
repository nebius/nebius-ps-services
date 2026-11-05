# Collection and Attribution

Read before connector access. These are reporting rules, not claims that every
Jira edition or MCP implements the same fields, functions or tool names.

## Inputs and Time

- Accept each public selector once, with a nonempty value. Reject unknown flags,
  positional arguments, invalid calendar dates and dates after local today.
  Preserve title text inside quotes; do not interpret it as JQL or a regex.
- Freeze one inclusive interval `[start, end]`, with end at collection start.
  Default start is end minus 90 times 24 hours. Explicit dates start at midnight
  in the agent's local IANA timezone; use UTC when unavailable and show the zone.
  Convert offset-bearing event timestamps to comparable instants. Honor DST.
- Keep Jira's query timezone distinct from the report timezone. Translate the
  lower bound when known. When unknown, widen candidate discovery by two calendar
  days. Apply exact interval filtering to event timestamps, never day-level
  search matches alone. Unknown/offsetless timestamps need a documented source
  timezone; otherwise they cannot establish interval eligibility.

## Capabilities, Site and Identity

Discover available schemas for current identity, accessible sites, user lookup,
issue search/read, comments, changelog, and pagination. Use deferred discovery
only when the host exposes it. Classify operations by effect: allow search and
read operations only, including through generic executors. Do not call write
operations or enable recently viewed history as a side effect of reading.

Select the site established by the user's context, otherwise the sole accessible
Jira site. Ask when more than one remains plausible. Missing connection or
search/read access produces a concise blocker; do not install or repair access.
The scope is all accessible projects at that site, not all company data.

Default to the authenticated account. Resolve `--user` to exactly one stable
identity using existing lookup capabilities. A Cloud account ID, a Data Center
user key or another documented stable connector identity can serve this role.
Do not assume Cloud accountId/cloudId fields on every connector. Email may be
hidden; a lookup that demonstrably maps the supplied selector to a single
account is acceptable without redisplaying its email. Fuzzy name results alone
are not exact email matches. Ask for a verified ID or account selection if
ambiguous; never derive names from email prefixes or merge identical names.

## Candidate Discovery

1. Use exhaustive paginated issue search over a conservative updated-since
   boundary, including all statuses and accessible projects. Discover fields
   needed for stable identity, key, URL, summary, created/updated, status and
   relevant actors. Request only needed fields, not every custom field.
2. Do not impose `updated <= end`: an issue changed during collection can still
   contain earlier qualifying activity. Exclude post-end activity locally.
   Use a supported stable ordering and deduplicate immutable IDs across pages.
3. Match `--title` against the retrieved summary as a Unicode case-insensitive
   literal substring. JQL full-text/phrase matching is not equivalent. Do not
   use a server title prefilter unless it is demonstrably a superset of the
   literal match; otherwise it could omit matching titles. Missing titles with
   a title selector cannot be classified; report that coverage gap.
4. Fetch needed comments and field history for every title-matching candidate.
   A focused assignee/reporter/updatedBy search may help find early rows but
   cannot replace the broad scan: someone else may have recorded the credit.
   Native updatedBy has day granularity and includes updates not visible as
   retained comments. Search matches alone do not establish actor/date evidence.
5. If the connector only provides ranked/semantic search, retain proven rows
   with partial discovery coverage. Do not call relevance results exhaustive.

Follow each tool's actual pagination contract, including cursor, offset, totals
and terminal markers. An embedded comment/changelog list is not automatically
complete. Use a dedicated paginated tool when exposed; otherwise compare
returned counts/markers and mark the stream partial or unknown. Never invent a
pagination parameter or silently obtain credentials for a direct REST fallback.
Do not request counts separately when a cursor terminal already proves exhaustion.

Process pages incrementally and retain compact per-issue evidence: site/issue ID,
key, current title/status, actor identity, event kind/time, source reference and
coverage. Do not save raw responses, comments or a persistent report cache.

## Qualifying Evidence

Require a verified subject identity and an in-window timestamp for each event.
The selected user can have several qualifying roles; a ticket still gets one row.

| Evidence | Qualification and date |
| --- | --- |
| Creation | Issue creation with the target as verified creator qualifies at created. |
| Initial reporter/assignee | Qualifies at creation only if an initial snapshot or complete history proves that initial identity. Current fields alone do not prove it. |
| Reporter/assignee change | A dated history entry moving the role **to** the target qualifies. Removal by another actor does not. |
| Authored field change | A dated changelog entry authored by the target qualifies, including a resolution/status transition; another actor's change does not transfer credit. |
| Authored comment | Every comment with target author qualifies at comment.created, including brief acknowledgments. |
| Comment edit | A separate event qualifies at comment.updated only when updateAuthor is verified as the target. Do not refresh authorship using another editor's timestamp. |
| Explicit third-party credit | A comment clearly crediting completed contribution/resolution to the verified target qualifies under the credit rules below. |

Current assignment, reporter, status or resolutionDate alone cannot identify a
dated actor. Search hits, watchers, votes, bare mentions, requests and assignment
removal by someone else are not evidence of qualifying activity. If the target
performed the removal, their own authored field change can independently qualify.

For third-party credit, require affirmative attribution of performed work to
the target, not a request, speculation, negation, quoted hypothetical or a vague
shared name. A structured account mention can identify the subject; plain text
needs unambiguous corroboration against the resolved account. Keep a short
evidence reference internally; do not reproduce comments in the report.

Use an unambiguous stated work date when available. A date-only work claim
qualifies only when its entire local calendar day lies within the interval;
otherwise boundary membership is uncertain and the claim cannot qualify alone.
Do not invent a time of day; display the date and sort such events conservatively
at that day's start, breaking ties by key. If no work date is stated, use the
comment's creation time as **credit recorded on**, not actual effort time.
An explicitly out-of-window work date must not become in-window through this
fallback. Ambiguous relative dates remain uncertain; do not substitute a credit
date to defeat known timing uncertainty. The evidence itself must have been
recorded by end. If edits may have introduced the credit after end and no
historical body is available, do not use that body to prove pre-end credit.

If several events qualify, choose the latest dated evidence. Do not infer
resolution from assignment, a teammate's status change, or unverified comment
claims. A fix credited in a comment can establish involvement while the ticket
remains Open; display Jira's actual observed status.

## Coverage and Failures

- Track candidate discovery, identities, fields, comments and history separately.
  A proven row survives other incomplete streams, but its displayed date is
  only the latest **observed** qualifying event when later evidence may be missing.
- Complete means exhausted discoverable inventory and required evidence streams
  within this account's accessible, retained data. It never certifies invisible,
  deleted or otherwise unavailable history, or a company-global inventory.
- Mark partial for known missing permissions/fields, response caps, failed or
  truncated pages, unsupported pagination, repeated nonadvancing cursors, search
  instability, contradictory observations or an interrupted scan. Never silently
  stop at an arbitrary result count or infer an exhaustive absence.
- Retry a transient read at most twice, respecting a returned retry delay when
  feasible. For a persistent failure or rate limit, return observed rows and the
  remaining gap. Do not loop, change credentials or restart successful collection.
- Missing evidence timestamps cannot be guessed from issue.updated. Exclude
  unproven rows and explain the gap; do not fill the table with speculative rows.
- Current titles/statuses are retrieved observations, not an atomic historical
  snapshot. Detect observed page/content movement, preserve the frozen event
  interval and report resulting uncertainty rather than moving the interval.

## Official Sources and Applicability

Verified during authoring, 2026-11-03:

- [Atlassian MCP supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools): current documentation describes discovery, Jira reads/search and paginated comment/history capabilities. Availability depends on connector version and permissions; discover actual schemas.
- [JQL fields](https://support.atlassian.com/jira-software-cloud/docs/jql-fields): Cloud date searches use configured Jira timezone; summary search semantics are not this skill's literal substring rule.
- [JQL functions](https://support.atlassian.com/jira-software-cloud/docs/jql-functions): updatedBy includes field/comment activity, has day granularity and records only the last comment edit.
- [Profile visibility](https://developer.atlassian.com/cloud/jira/platform/profile-visibility): profile fields such as email may be hidden; never require unrestricted email access.

These Cloud references are examples, not a promise of Data Center API parity.
Use equivalent verified connector capabilities on other editions; unsupported
capabilities remain explicit limitations. No particular tool prefix, endpoint,
plugin extension, authentication flow or custom field is required.
