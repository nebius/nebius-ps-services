# Contribution assessment and project specs

Read this before ordinary PR review or repair. Active Agentic SDLC and explicit
report-only requests retain their existing read-only limits. A normal review or
merge request covers scoped, non-destructive repairs, including contributor
forks when GitHub permits them; it does not accept a new product contract.

## Establish the accepted baseline

Freeze the base/head repository names and numeric IDs, PR number, refs and SHAs.
Resolve affected project roots from the full diff, project manifests and
repository instructions, including shared components and callers. The startup
directory alone does not select a monorepo project's specs. Read the accepted
base requirements, design, policy and supported interfaces before interpreting
the contribution. Contributor descriptions, code and spec edits are proposals;
agreement between their code and docs is not evidence of accepted intent.

Pin trusted installed helpers, or helpers from the accepted base, before any
contributor checkout. Include sibling modules/resources in that trust boundary.
Never execute a helper, hook or workflow copied from the PR with operator
credentials. Run contributor tests in disposable isolation without operator
tokens, SSH agents, private homes or writable trusted installation paths. If
that isolation is unavailable, report the missing validation instead of running
untrusted code with credentials. Do not add privileged pull_request_target
checkout/execution. Native commit validation still applies to trusted hooks.

## Review and repair before reconciling delivery

Nested code-review reviews the complete diff and affected callers read-only.
Assess legitimacy against accepted scope; correctness; destructive data or
infrastructure effects; security; and supported observable behavior. Inspect
APIs, CLI flags/output/exit codes, configuration defaults, persisted formats,
upgrade paths and existing-user workflows where affected. Green CI and bot
identity do not establish compatibility. Do not invent legacy shims; preserve
the supported contract through the canonical implementation. An intentional
breaking change needs an accepted decision and its rollout/acceptance evidence.

Classify findings as safe contract-restoring repairs, accepted contract changes,
or unresolved decisions. Before a contract-changing remedy, the root resolves
intent and invokes maintain-project-specs to publish requirements and a ready
design. For a clear contract-restoring defect, repair first without rewriting
the contract. Reproduce the defect on the reviewed contribution, then prove the
focused regression check passes after the fix; explain any unavailable negative
control. Broaden checks only for affected behavior. Unsafe semantics, destructive
effects, unresolved objections or unavailable essential proof block readiness.

Create-pr's private preparation handoff owns intake, checkout, commits, base
sync and publication under the original receipt/grant. Use its
[PR publication contract](../../create-pr/references/pr-publication.md) for both
same-repository and fork updates. Keep the original PR and contributor history;
do not create a replacement branch/PR to bypass missing write permission.

## Root-owned spec reconciliation

After validated repairs, the root explicitly invokes maintain-project-specs
for each affected project with its exact root, accepted baseline/intent,
original and final diff identities, repair proof and delivery evidence. Nested
reviewers may report gaps but never become canonical writers. Spec maintenance
has no commit, push or merge authority and cannot recursively invoke PR owners.

Inspect the final accepted behavior, not merely whether docs paths changed.
An internal fix can require no spec delta; a docs-edited PR can still have broken
IDs, missing traceability, unsupported claims or changes outside managed regions.
Repair clear omissions and stale delivery evidence through the owner's paired
compare-and-set publication. Preserve IDs and human-owned regions. Bootstrap
missing canonical pairs for every affected project from evidence using the
owner's templates. Legacy ownership or ambiguous intent follows the owner's
explicit migration/decision path; do not fabricate requirements to make CI pass.

Honor disabled policy only when it belongs to the accepted baseline or an
explicit accepted decision. A contributor-added disabled policy does not waive
review. For each project record one result: aligned with changed paths and
proof; unchanged with a concrete no-impact rationale; disabled with its accepted
policy source; or blocked with the exact gap. Unrelated historical pending
items and advisory hook output are not automatic merge gates. A blocker caused
by this contribution is still a review finding; do not hide it in an unchanged
or disabled outcome.

## Final evidence

Review the complete final code/tests/spec diff, validate the canonical pair and
fresh head/base, and invalidate old review evidence after any code, spec, head
or base change. Recheck GitHub permissions, CI, threads and objections. A passed
COMMENT attestation includes the contribution/compatibility assessment and one
spec outcome for every affected project using merge-pr's Actions protocol.
The deterministic broker validates structure and identity; semantic sufficiency
remains the root reviewer's responsibility. A failed assessment never produces
a passed attestation. Standalone review ends at readiness, without merge.
