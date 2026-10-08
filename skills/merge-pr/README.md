# Merge PR

`merge-pr` verifies and merges a GitHub pull request outside the Agentic SDLC
workflow. It is the general-purpose merge primitive used by release publishing
skills after `create-pr` opens or reuses a release-prep PR. It reports merge,
destination verification, and post-merge CI as separate outcomes.

## Usage

```text
$merge-pr [<PR-number|PR-URL|current-branch>] [--merge-method squash|merge|rebase] [--delete-branch]
$merge-pr --help
```

Invoke only with explicit merge intent. The default method is squash for
ordinary branches; keep the head branch unless deletion is explicitly requested.
`-h` also shows help. No additional public flags.

## What It Does

- Resolves a PR from a number, URL, or current branch.
- Waits for PR checks when they are pending.
- Verifies review state, draft state, mergeability, and head SHA.
- Requires local review-pr evidence and safe repairs before dispatching a
  trusted default-branch GitHub Actions workflow. The async API guards the exact
  reviewed head. Required merge queues stop for a maintainer.
- Refuses admin bypasses and branch-protection overrides.
- Records GitHub's actual resulting commit and verifies its ancestry in the
  remote target branch, allowing the target to have advanced.
- Uploads intent before effects and recovers exact-result CI/Pages after merge.
- Observes applicable CI on the exact resulting commit for up to one hour.

## Architecture

```text
Pull request
  |
  v
Readiness checks
  |
  v
Head SHA guarded merge
  |
  v
Authoritative outcome
  |                  |
  v                  v
External queue       Merged result SHA
  |                  |
  v                  v
Read-only blocker    Remote destination ancestry
                     |
                     v
                     Result-commit CI (up to 1 hour)
```

## Core Concepts

- The user or calling skill must have explicit merge intent.
- `squash` is the default merge method for ordinary protected branches.
- GITHUB_TOKEN cannot enqueue merge queues; there is no credential fallback.
- Ordinary create-pr and complete publish-* requests delegate merge authority.
- Actions approves after local exact-head/base review. Agents run locally;
  Actions runs deterministic scripts. See [setup/protocol](references/actions-protocol.md).
- Required reviews, branch protection, and environment rules are blockers, not
  conditions to bypass.
- This skill does not write Agentic SDLC state; `sdlc-merge-pr` owns that path.
- The guarded PR head and resulting merge SHA can differ, especially for squash
  and rebase. Verification uses the resulting SHA supplied by GitHub.
- CI polling uses a 3600-second deadline per invocation and a 30-second interval;
  delayed checks and observed reruns do not reset it. Result settlement is
  bounded to 60 seconds. Local verification is read-only; a separate trusted
  completion workflow dispatches exact-result CI and configured Pages builds.
- CI reports passed, failed, pending, not configured, or unverified. Neutral and
  skipped checks are disclosed; accepted check policy is not proof tests ran.
  Unknown configuration or access errors cannot become "not configured."
- `merged; destination verified; CI failed` preserves both outcomes. Queued
  requests have no post-merge CI result and defer requested branch deletion.
- Verification requires readable PR, reference, check/status, Actions, and
  applicable provider/configuration evidence. Missing visibility is reported;
  the skill does not expand permissions or replace credentials.

See [completion verification](references/completion-verification.md) for API
recipes, queue races, conclusion rules, cleanup conditions, and official sources.

## Evaluation

The canonical trigger CSV retains invocation-selection coverage. Quality cases
replay synthetic local API observations, including rewritten commit identities,
destination advancement, queue removal, and CI failures, without contacting
GitHub. Fixture timestamps simulate deadlines without real waits. Compare with
the pre-edit skill using the catalog's isolated quality runner when authenticated.
Static definitions, copied installation, native triggering, and output quality
are separate evidence lanes; none proves a live merge occurred.

## Files

- `SKILL.md`: Merge workflow, guardrails, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
- `references/completion-verification.md`: Required completion evidence rules.
- `references/actions-protocol.md`: Authority, installation and recovery contract.
- `scripts/merge_gate.py`: CI aggregate, review admission and protected merge.
- `scripts/merge_completion.py`: Result CI, Pages and durable reconciliation.
- `evals/trigger-prompts.csv`: Invocation selection cases.
- `evals/evals.json` and `evals/fixtures/`: Completion behavior cases and inputs.

## Reviewed dependency updates

Dependabot updates, including Docker, require complete local review, meaningful
validation and fresh evidence after every head/base change. Safe source repairs
use create-pr. Bot metadata and green CI alone cannot authorize merging.
Only trusted explicit dispatch initiates a merge; scheduled events observe CI
and recover already-authorized completion.
