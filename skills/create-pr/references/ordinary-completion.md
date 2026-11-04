# Ordinary PR completion

This contract excludes Agentic SDLC. The operator starts work on the local host;
GitHub Actions runs only deterministic verification and authorized effects.
Dependabot updates, including majors and Docker, use the same review and safe
repair path. Bot metadata never substitutes for review. No scheduler or unattended
agent initiates review or merging; existing completion schedules recover only
previously authorized operations.

Read [checkout completion](checkout-completion.md) before intake and outer return.

1. Use the live default branch. Reuse the current feature branch; when on the
   default, begin canonical create-pr intake before creating the frozen feature
   at the receipt HEAD. Carry dirty changes with it. Never push the default.
2. Prepare, validate and commit through the canonical transaction (root
   `git add -A`, normal hooks), then synchronize the base. Before push or PR
   creation, freeze the final head/base and invoke nested read-only code-review
   on the complete proposed PR diff and relevant callers/tests. The parent owns
   bounded safe repairs, renewed validation and review. Preserve unrelated work;
   if whole-tree staging would absorb unrelated changes, resolve scope first.
   Publish only the reviewed candidate and open/reuse its PR.
3. Invoke local review-pr to coordinate readiness. It delegates frozen diff
   analysis to code-review or reuses it only within this root operation at the
   exact unchanged head/base. Always refresh GitHub identity, requirements,
   checks, reviews, human objections, unresolved threads and mergeability.
   Safe means a supported, bounded repair with a clear expected result and
   relevant validation. Stop for semantic choices, unsafe changes, unresolved
   human requested changes, or missing authority. Never self-dismiss objections.
4. For each safe repair, reuse the same create-pr grant through its **private
   preparation handoff**: validate/commit/synchronize, review the complete final
   candidate, push/update the existing PR and return its new head. This handoff
   may use nested read-only code-review but never invokes review-pr or merge-pr,
   posts a passing GitHub review or closes the caller's grant. It is an internal
   calling contract, not a public flag. Head/base movement invalidates analysis
   and attestation; the parent re-evaluates readiness after the handoff.
5. After local review and checks pass, review-pr records the exact head/base
   with the structured COMMENT attestation described by merge-pr when eligible.
   This is an authenticated operator attestation, not independent proof of model
   execution. Ineligible/report-only reviews return findings without admission.
6. **Plain create-pr stops at the reviewed PR, completes local checkout and closes its grant here.** Only
   explicit semantic intent to merge delegates merge-pr with the PR, exact
   head/base, review ID and original authority. An explicit action request such
   as "Can you create a PR and merge it?" authorizes the requested sequence.
   Negation, discussion, informational questions about skill behavior, quotes or
   base-to-branch synchronization do not authorize PR merging. For
   multiple PRs, preserve dependency order without assuming earlier PRs merged.
   Complete publication callers retain ownership, bind their checkpoint to the
   final reviewed head and then delegate merge-pr. If later repair or sync changes
   that head, return fresh evidence to the publication owner for guarded checkpoint
   reconciliation before dispatch. Publication preparation-only modes still stop
   at their selected boundary.
7. Merge-pr invokes parent-owned review-pr for every unmerged PR, refreshes state
   and dispatches the protected Actions workflow only after readiness passes.
   Parent-owned review-pr returns evidence without re-delegating merge-pr.
   An already-merged PR uses observation and exact-result verification only.
   Preserve the original receipt, grant, targets and readiness deadline across
   every handoff. Only the completion owner closes at the selected boundary.
8. The merge/completion owner observes until actual merge or the fixed
   86,400-second limit; pending is not completed and the grant stays open.
   PR readiness, merge settlement and post-merge verification each have an
   independent fixed 86,400-second (24-hour) budget. Use publication checkpoint
   deadlines when supplied, even when shorter. Repairs and reruns never reset
   them. An explicit user continuation may start a new ordinary observation
   attempt, retaining the original transaction and prior outcome; only explicit
   release resume renews release-owned budgets. Preserve checkpoints, leave GitHub work running
   and report the exact resume command on timeout. Keep short API timeouts,
   bounded polling and minute progress. Verify exact-result CI and configured
   Pages through the completion helper before reporting complete. Record confirmed
   merge results immediately and perform outer local completion independently of
   failed/timed-out result verification; pending verification keeps the grant open. Missing setup,
   permissions, merge queues or unsupported workflow-file merges stop for a
   maintainer; never use PAT, admin, auto-merge, direct push or CLI merge fallback.

Plain review-pr reviews/repairs and returns readiness without merging. A root
combined review-and-merge request delegates **once, before review work**, to
merge-pr, which calls parent-owned review-pr and then protected merge. The child
never interprets the root merge request as permission to recurse. Explicit
complete publish-release, publish-helm and publish-image requests authorize their
necessary ordinary PR and merge steps without another routine prompt. Help,
report-only and Agentic SDLC restrictions always propagate.
