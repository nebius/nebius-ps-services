# LOC Modifications report

Read this reference only for LOC mode, or its owner-selection section when
normal activity mode uses `--owner`. This is change-volume reporting, not a
ranking of contribution quality or personal productivity.

## Public interface

```text
$github-report --loc-modifications-report [--owner OWNER | --repo OWNER/REPO[,OWNER/REPO...]] [--date YYYY-MM-DD] [--top K] [--user TEXT]
```

Validate before any tool access. `--owner` and `--repo` are mutually exclusive.
`--top` is a positive integer used only in LOC mode; omitted means all matches.
Reject `--project` in LOC mode. Repeated flags and malformed selectors fail.
No new executable, authentication, service or storage is required.

Use the existing bundled entrypoint with the same selectors:

```bash
python3 <skill-directory>/scripts/github_report.py \
  --loc-modifications-report --owner example-org --top 3 --date 2025-11-24
```

`--date` is the start at local midnight, not the end date. The end freezes at
collection start. Without a date, retain the rolling 90-day default. Without
an owner/repository selector, resolve the checkout using the existing rules.

## Owner selection

Resolve `repositoryOwner` as Organization or User and freeze its stable ID.
Page `repositories` with `ownerAffiliations: [OWNER]`, including forks, archives
and all accessible visibility levels. The viewer's memberships, collaborator
repositories and an organization's member directory are not owner scope.
Verify each repository's stable owner ID and canonical name; deduplicate IDs.
An inaccessible owner is unavailable, never an empty complete inventory.

Use one authenticated numeric viewer identity across transports. Existing
authentication defines visibility; never repair auth or expand access. Page
failures remain partial, including when no matching users are found. Propagate
owner inventory limits into either report mode. Report visible repository
coverage separately from unknown hidden repositories.

## Collection and calculation

Read only merged PR identities, repository identity, merge/update timestamps,
User author identity/name and whole-PR additions/deletions. Do not hydrate
reviews, comments, issues, backlog, repository code or folder inference.
Use fixed GraphQL documents and JSON variables through the existing transport,
with its shared 2,000-request budget and 45-second request timeout.

Page merged PRs by descending `updatedAt` in bounded 25-item pages. Validate
monotonic ordering and `updatedAt >= mergedAt` before stopping below the start.
An old merge with a recent update cannot stop discovery. Filter exact
`mergedAt` against the frozen interval. Reconcile a second bounded pass for
PRs updated since collection started, unioning stable PR IDs. Changed counts,
duplicates, moved/new evidence, invalid order, null nodes, repeated cursors,
errors and incomplete pages remain explicit partial coverage. This reduces
pagination races; it does not make GitHub history an atomic snapshot.

For each stable API User author, sum nonnegative integer additions plus
deletions across qualifying PRs and repositories. Count a zero-diff PR as a
merged PR with exactly zero modifications. Exclude API Bot accounts; missing
author identity remains an unassigned coverage limit. Public names may be null;
missing names are unknown for substring matching. User accounts may still be
service accounts and are not independent proof of human employment.

The metric includes all tracked text-file types, documentation, generated
files, lockfiles, notebooks, data, imports, formatting and repeated changes.
A replacement usually contributes one deletion and one addition. Attribute
the whole diff to its PR author as a reporting convention, never as proof that
the author personally wrote each line. Do not add review/coauthor credit or
sum per-commit diffs. Source-only exclusions require a separately defined
contract and must not be inferred from a sample of winning PRs.

## Ranking and output

Aggregate the full observed scope before applying the literal `--user`
login/public-name substring and optional top K. Sort descending by observed
LOC, then casefolded login and stable ID. Equal totals share the same rank;
report when top K cuts a tie. Never stop collection after finding K accounts.

Use the emitted `markdown` unchanged for numeric rows and coverage. The table
has Rank, Contributor, **LOC Modifications**, and Merged PRs; never separate
additions/deletions columns. State scope, frozen local interval and metric
definition. No per-repository activity tables, shared backlog or activity
finalizer is used in this mode.

Partial inventory, pagination, identity or metrics means a **provisional
ranking among observed accounts**, even if the displayed accounts' own
metrics are complete. Unknown metrics are not zero: show a known lower bound
when some PRs have metrics, otherwise unknown. Retain qualified empty results.
The JSON contains mode-specific schema version 1, coverage, per-repository
limits and rows; it is separate from normal activity schema version 3.
Exit 0 means complete observable collection, 3 partial evidence, 2 unavailable
or invalid input. Do not save reports or real user data automatically.

## Verified primary references

Reviewed 2026-11-24; verify live connector schemas before choosing transport.

- [Repository owner and repository connections](https://docs.github.com/en/graphql/reference/repos)
  define ownership filters and pagination.
- [User identities and repositories](https://docs.github.com/en/graphql/reference/users)
  distinguish owner affiliations from viewer affiliations.
- [Pull requests](https://docs.github.com/en/graphql/reference/pulls)
  define merge timestamps and whole-PR additions/deletions.
- [GitHub CLI API](https://cli.github.com/manual/gh_api)
  documents JSON-input requests and pagination.

Search counts may corroborate collection; they are not complete pagination
proof. Never infer author identity or missing metrics from search snippets.
