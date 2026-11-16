# Ordinary PR completion

This contract excludes Agentic SDLC. The operator starts work on the local host;
GitHub Actions runs only deterministic verification and authorized effects.
Dependabot updates, including majors and Docker, use the same review and safe
repair path. Bot metadata never substitutes for review. No scheduler or unattended
agent initiates review or merging; existing completion schedules recover only
previously authorized operations.

Read [checkout completion](checkout-completion.md) before intake and outer return.
For issue-plan handoffs also read the shared
[issue-resolution contract](../../worktree/references/issue-resolution.md).
After final readiness and before return/merge, verify concise issue comments and
render/read back the eligible native closing references. Any later head change
requires removing stale owned references before push and fresh validation.
Keep the handoff identity with the existing outer operation; no new coordinator
or implicit implementation/publication authority is introduced.

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
4. Before checkout-dependent review or any repair, call the owner's
   [private review execution](private-review.md) handoff. Reuse the same create-pr
   grant and its subordinate execution context through **private preparation**: validate/commit/synchronize, review the complete final
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
8. Observe authoritative merge settlement under the fixed ordinary 86,400-second
   budget or inherited publication deadline; acceptance alone is not merge.
   Record the merge immediately and verify containment in destination history.
   Default ordinary completion returns here, with result CI and Pages reported
   separately. Only explicit `merge-pr --wait-for-result` waits for those results.
   Publish-release owns its selected service qualification, and SDLC retains its
   accepted delivery boundary. Repairs and reruns never reset a deadline.
   Guarded local completion is independent of asynchronous CI. Preserve concurrent
   user checkout changes and report a local-completion blocker without undoing
   those changes or falsely closing the grant. Missing setup, authority or required
   queue support remains a maintainer blocker; no bypass or credential fallback.
