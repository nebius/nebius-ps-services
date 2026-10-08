# Completion Verification

Read before executing `merge-pr`. All verification is read-only. Use the frozen
PR host, base repository, branch, and number, not an implicit local remote or
assumed `main`. Use `gh pr` with the resolved PR URL. Use `gh api --hostname`
with the resolved host for REST/GraphQL requests; never print credentials.
Quote shell arguments, pass GraphQL variables separately, and URL-encode branch
names in REST paths. Do not execute repository content to discover CI policy.

## Read Queries and Timing

Use the endpoints below through `gh api --method GET --hostname <host>`;
GraphQL uses `gh api graphql --hostname <host>` with a read-only query.
These are API recipes, not additional public skill flags. Paginate lists and
retain only relevant identities, states, conclusions, timestamps, and links.
Do not dump complete PR bodies, patches, logs, or credentials into reports.

| Evidence | API and fields |
| --- | --- |
| PR and result | `GET /repos/{owner}/{repo}/pulls/{number}`: `merged`, `merged_at`, `state`, `merge_commit_sha`, `head.sha`, `base.repo`, `base.ref` |
| Remote target tip | `GET /repos/{owner}/{repo}/git/ref/heads/{branch}`: `object.sha` |
| Destination ancestry | `GET /repos/{owner}/{repo}/compare/{result_sha}...{target_tip_sha}`: `status`, `merge_base_commit.sha` |
| Checks | `GET /repos/{owner}/{repo}/commits/{result_sha}/check-runs?filter=latest&per_page=100`: IDs, app, name, `head_sha`, status, conclusion, suite, links |
| Commit statuses | `GET /repos/{owner}/{repo}/commits/{result_sha}/status?per_page=100`: latest contexts, states, URLs; inspect entries, not just combined state |
| Actions runs | `GET /repos/{owner}/{repo}/actions/runs?head_sha={result_sha}&per_page=100`: workflow/run IDs, `run_attempt`, `head_sha`, `head_branch`, event, status, conclusion, links |
| Workflow configuration | Contents API for `.github/workflows` at `ref={result_sha}`, plus active workflow/provider configuration and relevant repository policy |

Bound individual reads to at most 30 seconds using the executing host's process
deadline. Retry transient failures with backoff, respecting `Retry-After`, only
within the remaining budget; permanent access/schema errors are unverified.
If a rate-limit delay exceeds the remaining budget, report the limitation.
Never turn a failed/truncated page or an unsupported field into an empty result.

Start a 60-second settlement window after the command returns (including an
ambiguous error), or when first inspecting an already-merged/queued PR. Poll
unsettled state every five seconds within this window. Do not reset it after a
successful read. If merged state becomes known, start the per-invocation
3600-second CI deadline then; result settlement consumes that budget too.
Budget sleeps and API calls against the absolute deadline. No persistent
cross-invocation local timer is introduced. The deployed completion workflow
separately reconciles retained intent receipts while the laptop is offline.

## Actions Broker Result Evidence

For a merge carrying a `merge-intent/v1` receipt, use the installed completion
helper with the trusted deployment policy and original broker run/attempt:

```text
python3 <merge-pr>/scripts/merge_completion.py verify --repo owner/repository \
  --policy <trusted-merge-policy.json> --pr 123 --head <reviewed-head> \
  --run-id <broker-run> --run-attempt <broker-attempt>
```

This command is read-only. It checks authoritative merge/destination identity,
trusted intent provenance, applicable explicit result-CI runs and their latest
attempt artifacts, and configured Pages publication. Resolve numeric operator
allowlist configuration from the trusted repository before using it. Exit 2 is
pending; exit 1 is blocked/unverified. Neither permits repeating a merge.

GITHUB_TOKEN merges suppress ordinary push-triggered CI and legacy Pages builds.
The completion workflow owns explicit dispatch and Pages build requests. Its
CI runs use a trusted default workflow revision, which can differ from the
tested result SHA. Require the exact-result artifact, correlation, source
identity and successful push-equivalent jobs; do not filter these runs out
because their event is `workflow_dispatch` or their `head_sha` is newer. The
helper validates the tested checkout independently. A failed latest attempt
cannot be hidden by an earlier success. A successful Pages build must contain
the result and still use the configured default/root source.

Expired/missing intent, uncertain effect journals, permission errors and failed
CI/Pages are explicit reconciliation blockers. The local skill does not rerun
those effects. For a merge by another actor without a broker receipt, use the
ordinary result-SHA/provider evidence rules below. Never invent a receipt or
assume that this broker caused an externally observed merge.

## Merged Result and Destination

1. Require authoritative `merged: true` and a merge timestamp. If sources
   disagree, refresh within the settlement bound and report unresolved evidence.
   A successful CLI exit, closed PR, or non-null SHA alone is insufficient.
2. Read `merge_commit_sha` only from the merged PR response:

   | Method | Result represented by `merge_commit_sha` |
   | --- | --- |
   | Merge | The actual merge commit |
   | Squash | The squashed commit on the base branch |
   | Rebase | The commit the base branch was updated to |

   Before merging, this field may be a test merge commit. Never substitute the
   original PR head, a queue entry's synthetic commit, or the newest branch tip.
   If missing after settlement, report `merged; destination unverified; CI
   unverified` with the missing identity. Do not issue another merge command.
3. Reconfirm the observed base repository/ref matches the frozen target. A
   changed target is a verification failure, not permission to verify elsewhere.
4. Snapshot the remote target tip and compare immutable SHAs in the order
   `result_sha...target_tip_sha`. Accept only `identical` or `ahead` with
   `merge_base_commit.sha == result_sha`. `behind`, `diverged`, or a different
   merge base fails containment. Missing fields/API access remain unverified.
   Do not scan a paginated commits list for ancestry or require tip equality.
5. Record the result SHA, target tip, comparison, and observation time. Normal
   target advancement is allowed. Refresh this proof before the final report
   after a long CI wait; a later force-push can invalidate current containment.
   If result identity and target are known, CI may still be observed even when
   containment fails, but keep the failure visible.

For a merge performed by another actor or before this invocation, do not invent
the merge method or claim the current agent issued a guarded merge. The API's
resulting SHA is still the authoritative identity. For queued execution report
the configured queue method when available, otherwise `queue-configured`.

## Queue Membership

This section observes queues created by other authorized actors. The built-in
Actions merge path cannot enqueue; a required queue is a maintainer blocker.

Query the exact PR using GraphQL variables for owner, repository, and number:

```graphql
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id number url state merged mergedAt headRefOid baseRefName
      isInMergeQueue isMergeQueueEnabled
      autoMergeRequest { enabledAt }
      mergeQueueEntry {
        id enqueuedAt state position
        pullRequest { id number url }
      }
    }
  }
}
```

- Require a non-null entry with its ID, enqueue time, state, position, and the
  matching PR identity; verify `isInMergeQueue` agrees. Read errors or
  contradictory fields are unverified, not proof of absence.
- Refresh immediately before returning queued. Report the evidence as of that
  observation and stop; do not wait for the queue to finish. Destination and
  post-merge CI are `not applicable`, and branch deletion is deferred.
- If the refresh shows merged, follow merged verification. If a previously
  observed entry disappears and the PR remains open/unmerged, report removed
  from queue. Without prior entry evidence, report membership not established.
- Closed without a merge is closed unmerged. Auto-merge enabled without an
  entry is delayed auto-merge, not queued. Do not enable/disable auto-merge or
  requeue to repair the observed state.
- A queue entry's head/base commits describe queue construction, not the final
  result identity. They need not equal the guarded PR head.

## Applicable Result-Commit CI

For non-broker CI, start with the resulting revision's workflow definitions and available CI
provider/repository configuration. Identify automatic CI applicable to the
target-branch update, including branch/path filters and configured downstream
workflows. Read configuration as data. Do not execute workflow code or trigger
jobs to discover whether they apply.

Query checks, statuses, and Actions runs on the exact resulting SHA. For Actions,
require matching `head_sha`, target `head_branch`, and an applicable event/workflow
identity. Correlate check runs with their app, suite, workflow/run, and attempt;
check names alone can collide. Exclude unrelated PR, synthetic `merge_group`,
tag, scheduled, and manual-only runs. The explicitly correlated broker result
runs above are a separate supported evidence path. Do not reuse green PR checks or checks on
a newer target tip. If provider/event provenance cannot be established, keep
that portion unverified instead of guessing from a name or timestamp.

Include applicable external status/check providers, not just Actions. Absence
of workflow YAML cannot establish absence of externally configured CI. If
configuration, provider identity, pagination, or visibility is incomplete,
disclose the gap and do not claim complete CI success or `not configured`.
An empty combined-status result can itself say `pending`; inspect its contexts.

Refresh observations every 30 seconds, stopping at the absolute 3600-second
deadline. Expected workflows/contexts that have not appeared remain pending;
an early empty response cannot establish absence. Select the latest attempt
per workflow/app/check identity and latest status per context, without mixing
providers or allowing a duplicate name to hide a failure. An externally started
rerun replaces its prior attempt for the current result but does not reset the
deadline; never start that rerun yourself.

### CI Outcome Rules

| Outcome | Rule |
| --- | --- |
| `failed` | A latest applicable check/status/run has `failure`, `error`, `cancelled`, `timed_out`, `action_required`, `stale`, or `startup_failure`. Preserve its actual conclusion. |
| `pending` | Expected evidence is missing, or an applicable latest attempt is queued, waiting, requested, pending, or in progress at the deadline. |
| `passed` | Applicability/visibility is complete, the expected set is nonempty and fully terminal, and all applicable latest results are `success`, `neutral`, or `skipped`. |
| `not configured` | Readable configuration and provider evidence affirm no CI applies to this branch update, including filter exclusions, and observed results do not contradict that evidence. |
| `unverified` | Required reads, provider provenance, applicability, or conclusions are unknown, inconsistent, inaccessible, or unsupported. |
| `not applicable` | The PR is queued or otherwise not confirmed merged; there is no post-merge CI observation yet. |

Report neutral/skipped results explicitly: accepted check policy does not prove
tests executed or passed. If every result is neutral/skipped, say that no
successful test execution was established. A completed check with a missing or
unknown conclusion is unverified, not pending forever or successful.

A known failure takes precedence in the aggregate; also disclose missing
visibility or other pending checks. Otherwise incomplete visibility is
unverified; with complete visibility, outstanding expected work is pending;
only then can complete terminal evidence pass. Stop early on conclusive failure,
pass, verified absence, or permanent unverified evidence. An unresolved transient
read failure at the deadline is unverified, not a pipeline failure. Include
outstanding check names, links, and start/deadline when reporting a timeout.

## Branch Cleanup and Reporting

Keep deletion out of the initial merge/enqueue command. If explicitly requested,
clean up only the PR's exact head branch after merged state and destination
containment are verified. Re-read its repository/ref and require it still matches
the guarded head before deleting; never delete the target/default/protected
branch or another actor's newer work. Already absent means already deleted.
The remote deletion must atomically require the expected head SHA at mutation
time; a prior read followed by unconditional API deletion is not safe. Git's
fully specified `--force-with-lease=refs/heads/<head>:<guarded-sha>` with an exact
deletion refspec supplies that condition. Resolve the single push destination
to the verified head repository, suppress implicit additional refs/tags, and
never use an implicit lease, `--force`, or a content-updating refspec. This lease
only protects explicitly requested deletion; it does not authorize history
rewriting. An intervening push must reject cleanup, never trigger a retry with
the newer SHA. If conditional deletion is unavailable, defer remote cleanup.
Local cleanup additionally requires no checked-out worktree or concurrent writer
and the expected local head; use non-forced deletion only when those conditions
are established, otherwise retain the local branch. Do not switch branches or
repeat a merge command for cleanup. Report remote/local cleanup separately.
Queue membership alone never authorizes cleanup.

Always report merge outcome, destination proof, CI, and deletion separately.
`merged; destination verified; CI failed` is truthful. `merged; destination
unverified` is not permission to rerun the merge. `queued` is not a completed
merge for publishing callers. Verification does not authorize fixes, rollback,
requeue, CI writes, credentials changes, or protection bypass.

## Official Sources

- [PR result semantics](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)
- [Remote reference](https://docs.github.com/en/rest/git/refs#get-a-reference)
- [Commit comparison](https://docs.github.com/en/rest/commits/commits#compare-two-commits)
- [Queue entry](https://docs.github.com/en/graphql/reference/pulls#mergequeueentry)
- [GitHub CLI merge behavior](https://cli.github.com/manual/gh_pr_merge)
- [Check runs](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)
- [Commit statuses](https://docs.github.com/en/rest/commits/statuses#get-the-combined-status-for-a-specific-reference)
- [Workflow runs](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository)
- [Workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Accepted check conclusions](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging)
- [Git deletion refspecs and explicit expected-value leases](https://git-scm.com/docs/git-push)
