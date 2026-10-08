# Publish Release

Publish a GitHub Release with one invocation. The skill commits current work,
pushes and merges its PR through repository protections, tags the verified merged
commit, and verifies the resulting release and assets.

## Quick start

Run from the application project with Git/GitHub CLI authentication and the
installed `create-pr`, `commit`, `merge-pr` and Worktree guard owners available:

```text
$publish-release --tag 1.2.3
```

Or invoke `$publish-release` and answer the version question when no unambiguous
prepared version exists. Complete publication and waiting are the defaults.
The invocation includes all reviewed and validated current repository changes,
including sibling project changes, through repository-root staging.

The skill reuses your feature branch. Starting on the default branch creates a
release branch before committing. It creates missing release setup assets when
needed. GitHub Actions builds and uploads artifacts from the tagged commit.

## Approval and resume

Local review-pr runs before Actions approval: safe fixes use create-pr, followed by
fresh validation and review. merge-pr dispatches the protected broker, which
supplies routine Actions approval. Unsafe findings and human objections stop.

Additional required human or release-environment approvals retain a ten-minute
wait with links and 15-second polling. Partial approvals do not restart that
clock; repository protection remains authoritative.

After timeout or interruption:

```text
$publish-release --resume
$publish-release --resume --tag 1.2.3
```

A tag disambiguates multiple unfinished releases. Resume verifies GitHub state,
keeps completed work, and starts a fresh waiting attempt. It works across agent
sessions without replaying old commit grants. Timeouts leave the PR/workflow
intact; no local background publisher is left running.

Checks, merge settlement and release execution have separate one-hour wait limits.
Failures, conflicting identities and missing assets produce precise blockers.
A queued PR, pushed tag or draft release is not a completed publication.

Repository discovery uses GitHub's canonical repository endpoint without a
trailing slash; nested release and workflow reads retain their resource paths.

## Workflow and ownership

```text
publish-release
  -> prepare release content
  -> create-pr: review, validate, commit all work, synchronize, push, PR
  -> local review-pr: safe repairs, revalidation, exact-head/base attestation
  -> merge-pr: protected merge, resulting commit and CI verification
  -> isolated clone: annotated tag, runtime check, record exact object, push
  -> tag workflow: build and publish assets
  -> download / version / digest verification -> Published + release URL
```

The release uses the exact verified merge result, even if the default branch
advances afterward. An isolated clone leaves your working checkout and local tag
namespace intact. A private checkpoint stores progress and exact tag metadata;
it supplies no commit, merge or approval authority.

## Configuration and partial operations

Use `--project-dir`, `--tag-prefix`, package/import/build metadata and the existing
workflow to identify the target. The default merge method is squash. A required
merge queue stops for a maintainer. Branches are retained by default.

`--mode setup` prepares reusable assets; `--mode prep` ends at the pushed PR;
`--mode publish` continues an existing merged release. `--no-wait` returns a
pending checkpoint when work remains. See `SKILL.md` for the complete public
interface and [orchestration](references/orchestration.md) for private calls.

The runnable shell template now exposes content preparation, local tag creation,
exact-object push and wheel verification. It does not commit or publish branches;
those effects belong to the skill's canonical create-pr transaction. Existing
project copies require explicit regeneration to adopt this contract.

## Validation

```bash
python3 -B -m unittest discover -s publish-release/scripts -p 'test*.py'
bash -n publish-release/scripts/publish-release-doer.sh
shellcheck publish-release/scripts/publish-release-doer.sh
```

Run from the skills catalog directory. Tests use disposable local Git remotes,
fake GitHub responses and a virtual clock. They do not publish a real release.
Native invocation and live publication evidence must be verified separately.

## Files

- `SKILL.md`: public workflow, authority, waiting and completion contract.
- `references/orchestration.md`: owner handoffs, exact helper calls and recovery.
- `scripts/release_session.py`: GitHub observation, deadlines and release recovery.
- `scripts/release_checkpoint.py`: private atomic checkpoints and locking.
- `scripts/publish-release-doer.sh`: deterministic release primitives.
- `assets/`: changelog, aligned runnable helper and release-workflow templates.
- `scripts/test*.py`, `evals/`: executable and agent-evaluation coverage.
