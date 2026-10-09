# Ordinary PR completion

This contract excludes Agentic SDLC. All agent reasoning and review runs on the
local host; GitHub Actions runs only deterministic verification and Actions effects.
Dependabot updates, including Docker, use this same review and safe repair path.
Bot metadata never substitutes for review; automatic events cannot start merges.

1. Use the live default branch. Reuse the current feature branch; when on the
   default, begin canonical create-pr intake before creating the frozen feature
   at the receipt HEAD. Carry dirty changes with it. Never push the default.
2. Prepare, validate and commit the whole repository through the canonical
   transaction (root `git add -A`, normal hooks), synchronize the base, push the
   selected feature and open/reuse its PR. Preserve unrelated work; if whole-tree
   staging would absorb unrelated changes, resolve scope before effects.
3. Invoke local review-pr on that PR. Inspect the full diff, requirements,
   checks, reviews and unresolved threads. Safe means a supported, bounded repair
   with a clear expected result and relevant validation. Stop for semantic choices,
   unsafe changes, human requested changes that are not resolved, or missing
   authority. Never self-dismiss human objections.
4. For each safe repair, reuse the same create-pr grant through its **private
   preparation handoff**: validate/commit/synchronize/push/update the existing PR
   and return its new head. This handoff never invokes review-pr or merge-pr,
   never posts a passing review and never closes the caller's grant. This is an
   internal calling contract, not a public flag. Re-review the full final diff;
   head or base movement invalidates the prior attestation.
5. After local review and checks pass, review-pr records the exact head and base
   with the structured attestation described by merge-pr. This is an authenticated
   operator attestation, not independent proof of model execution.
6. Public `--prepare-only` returns here and closes the grant. Default completion
   invokes merge-pr with PR, exact head/base and review ID. A publication caller
   may bind its release checkpoint here before merge; it keeps ownership of the
   grant until completion. Never dispatch before a release checkpoint binds the
   final reviewed head. If later synchronization or repair changes that head,
   return fresh review evidence to the publication owner for its guarded
   checkpoint reconciliation before broker dispatch. Unsafe findings return a
   blocker without merge.
7. Merge-pr dispatches the protected Actions workflow. The create-pr/publication
   completion caller observes the request until actual merge or the fixed
   10,800-second limit; pending is not completed and the grant stays open.
   PR readiness, merge settlement and post-merge verification each have an
   independent fixed 10,800-second budget. Use a publication caller's checkpoint
   deadlines when supplied. Gate changes, safe repairs and reruns never reset
   them; only explicit release resume renews release budgets. Preserve the
   checkpoint, leave GitHub work running and report the exact resume command
   on timeout. Keep existing short API timeouts, polling and minute progress.
   Exact-result CI and configured Pages must be verified by the completion
   helper before reporting complete. Missing setup/permissions, merge queues or
   unsupported workflow-file merges stop for a maintainer, never a PAT,
   admin, auto-merge, direct push or direct CLI merge fallback. Close the grant
   after the selected completion boundary or explicit cancellation.

Standalone review-pr uses this same private preparation contract for authorized
safe writable repairs and never merges. Standalone merge-pr invokes review-pr
when current evidence is missing; repairs use the same owner. Explicit complete
publish-release, publish-helm and publish-image requests authorize these necessary
ordinary PR steps without another commit/PR/merge prompt. A review-and-merge
request likewise authorizes review, safe repair and protected merge together. Preparation-only,
report-only, help and Agentic SDLC restrictions always propagate.
