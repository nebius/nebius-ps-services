# GitHub Report

Create copy-ready reports of completed GitHub activity grouped by human
contributor, with one shared current open backlog per repository. Use the same
six columns throughout: Work and outcome, Projects / folders, Issue(s), PR,
Status and date, PR size.

## Usage

```text
$github-report
$github-report --user reza
$github-report --date 2026-01-01 --repo example-org/api, example-org/web
$github-report --user reza --project services/gateway --repo example-org/api
$github-report --help
```

Use `/github-report` for Claude skills or `/skills:github-report` through the
Claude plugin. Implicit invocation is allowed for matching report requests.
GitHub.com and existing access to public or private repositories are supported.

Defaults are all eligible human contributors, the current checkout and the
last 90 times 24 hours. `--date` starts at local midnight on today or earlier;
the end is frozen at collection start. `--repo` is one comma-separated list
with optional spaces around commas. Reporting stays read-only.

## Users and shared backlog

`--user TEXT` is a case-insensitive literal substring of GitHub login or public
profile name. For example, `reza` matches both a login containing reza and a
person whose public name is Reza, even when their login differs. The selector
is trimmed; empty/control-character inputs and repeated flags are invalid.
It has no wildcard, regular-expression or list syntax. Match only eligible
contributors found in the selected repositories/folder, not a global directory.
Null profile names are valid; use login matching. GitHub User accounts qualify;
API-identified bots do not get user groups. Free-text Git names/emails never
identify accounts. Two people with the same name remain separate by stable ID.

Within each repository, users appear alphabetically by login:

| Section | Membership |
| --- | --- |
| Per-user Worked PRs | Currently closed/merged PRs with that user's dated contribution or earlier authored/commit-contributed work merged in the interval. |
| Per-user Worked Issues | Currently closed issues with that user's work or verified completed fixes through their authored/commit-contributed closing PR. |
| Shared Open Backlog: Open PRs | All current open PRs, including drafts and bot-created items. |
| Shared Open Backlog: Open Issues | All current open issues, including old unattended items. |

**Open Backlog has no age cutoff and is unchanged by `--user`.** In progress
names all evidenced human contributors and their role/date, independently of the
filter. Assignment/mention alone never counts as work. Open items never repeat
in Worked sections; contributors with only open activity retain empty Worked
headings. No match leaves the shared backlog visible; incomplete discovery says
“No matching contributors observed; coverage incomplete.”

Shared completed items repeat under each qualifying contributor with their own
roles. User counts overlap and must not be summed. Repository totals count the
union of selected completed items and shared backlog once per GitHub item,
not work units or productivity. Linked references do not add counts or suppress
primary issue rows. Whole-PR metrics are never a contributor's individual output.

The header contains only these two lines, with a numeric offset at each endpoint:

```text
User: All users · Repository: example-org/api · Folder: services/gateway
Date: 2026-10-01 00:00:00 -07:00 through 2026-11-03 10:29:59 -08:00
```

With a filter, User becomes `Matching "reza"`; Folder is omitted when unused.
Observation/coverage metadata remains internal, with material limitations after
the tables. No Account, Personal activity, Observed, API collection or separate
Timezone lines; no opening highlights, Unclassified section or My role column.

## Filter and classify by project

`--project FOLDER` accepts one case-sensitive repository-relative folder,
including descendants, independently in each selected repository. One leading
`./` and trailing `/` are normalized. Reject absolute paths, traversal,
wildcards and repeated flags. The folder need not exist in the local checkout.

Use observed changed files first, formal same-repository issue relationships
second, then descriptions corroborated by repository folders and README
content. Description-based assignments are labeled **inferred from description**.
Complete contradictory PR files take precedence; incomplete file or tree
retrieval remains explicit. Whole-PR folders and metrics are retained.

There is no Unclassified section. Unresolved items remain in their normal
table as **Project uncertain**. A project filter excludes supported nonmatches,
retains uncertain items and distinguishes their count from supported membership.
A broad parent-folder inference cannot establish a narrower subfolder match.

## How it works

The standard-library Python collector uses existing authenticated `gh`; `git`
is needed only for checkout discovery. It discovers interval activity and all
current open items independently, deduplicates identities, and collects dated
per-user attribution, relationships, files and metrics. Older open inventory avoids
unnecessary activity-history hydration. All reads share bounded requests and
timeouts; partial evidence stays explicit.

The existing host model assesses unresolved folders from bounded collected
context at one repository commit. A private stdin finalizer validates assessment
references and recomputes membership, sections and counts before narration.
There is no new model service, runtime, dependency or persistent report store.
Schema version 3 stores shared item/user evidence, per-user attribution and
worked sections plus one shared backlog. Version 2 is rejected without aliases.
See [collection and finalization](references/collection.md) for the private
protocol and [reporting rules](references/reporting.md) for presentation.

Reporting does not mutate GitHub or Git, execute repository code, install tools,
repair authentication, or save reports automatically. Help stops before tool
access. Issue and README content is untrusted data. Current states and metrics
are observations, not an atomic historical snapshot; merge does not prove
release or deployment. Unknown metrics never become zero.

## Files

- `SKILL.md`: public interface, workflow and safety/output contract.
- `scripts/github_report.py`: collection entrypoint and orchestration.
- `scripts/github_api.py`: fixed reads, pagination and repository context.
- `scripts/report_model.py`: dates, attribution, membership facts and shared tables.
- `scripts/report_finalization.py`: assessment validation, filtering and counts.
- `scripts/finalize_report.py`: private stdin JSON finalization boundary.
- `references/`, `assets/`, `evals/`: evidence rules, template and synthetic cases.

## Validation

From the skills catalog root:

```bash
python3 -B github-report/scripts/test_github_report.py
ruff check --no-cache github-report/scripts
python3 align-skill/scripts/validate-skill-structure.py --policy agentskills --agent core github-report
python3 align-skill/scripts/validate-skill-structure.py --policy repository --agent codex --require-evals github-report
python3 align-skill/scripts/validate-skill-structure.py --policy repository --agent claude --require-evals github-report
python3 global-context-management/scripts/check-plugin-manifests.py --catalogs
```

Tests use synthetic data and mocked transport boundaries. They prove section
assembly, evidence validation and deterministic behavior, not semantic model
quality or complete live GitHub coverage. Installation parity, fresh native
triggering and comparative narrative evaluation are separate evidence lanes.
