# Branch repair access

Use this after selection/live-label routing and before ordinary repair allocation
or declaring branch access unavailable. Help, terminal audits, Plan Mode,
report-only and active SDLC never gain repair authority. Enrolled execution stays
with its controller; a local observation cannot authorize a hosted credential.

## Canonical read-only preflight

Run the trusted installed commit owner's private command, without a transaction:

```text
python3 -B <commit>/scripts/commit_transaction.py inspect-pr-access --repository OWNER/REPO --pr NUMBER
```

This internal command adds no public review-pr flag. It performs bounded GitHub
reads only, with no Git command, fetch, checkout, private state, credential change
or probe push. Bind its observed actor, PR repositories/refs and head/base SHAs
to the selected review. Identity drift invalidates the observation.

Read the JSON `status`, `basis`, `reason` and permission `facts`, not just the
exit code. Exit 0 means an eligible or denied observation; exit 2 means unknown.
`push_verified` remains false: metadata cannot prove transport/token scope or
branch-protection acceptance. Missing helpers or unrecognized output remain
unknown; never substitute a fork's repository-level push flag alone.

The publication owner's shared assessment accepts either:

- Direct head-repository `permissions.push == true`; maintainer edits are not
  additionally required for a direct writer.
- A verified human actor, upstream `permissions.push == true` and PR
  `maintainer_can_modify == true`.
  Therefore head-repository `push == false` alone cannot deny a maintainer's
  access to that PR branch.

Missing/malformed relevant facts and failed API/authentication reads are unknown.
A positive supported route suffices; denial requires conclusive exclusion of
both routes. No permission check grants user/task authority or proves a safe
conflict resolution.

The built-in Actions `GITHUB_TOKEN` is restricted to its workflow repository.
It does not inherit a human maintainer's access to a contributor fork. The
shared helper accepts a known repository scope only from its trusted execution
owner; never infer token provenance from an environment variable name. Verify
the human actor for the maintainer route, including before publication. Unknown
actor or credential context is never proof of push capability. Keep the
controller's existing credential boundary and never substitute tokens.

## Decisions and status effects

| Needed branch work | Access/safety evidence | Result |
| --- | --- | --- |
| None | Any access result | Continue review; no branch-write-only blocker. |
| Safe bounded repair | Eligible metadata | Continue through private preparation without repeated approval. |
| Required repair | Denied metadata | Explain the observed denial and contributor action; use `blocked`. |
| Required repair | Unknown | Say unable to verify branch-write access, retain pending repair; use `blocked`. |
| Unsafe or ambiguous conflict | Any access result | Explain the semantic decision needed; use `blocked`. |

Continue useful read-only analysis when repair access is denied or unknown.
Assess comment/status-label access separately on the base repository. Apply the
canonical `blocked` label through the lifecycle owner only when authorized;
preserve unrelated labels and verify readback. If status writes are unavailable,
report the comment/label effect pending, never as applied. This reference grants
no label creation, merge, replacement-PR or closure authority beyond the parent.

Report contribution value, correctness, merge conflicts, repair access, status
effects and actual publication separately. Eligible metadata is not permission
to resolve ambiguous conflicts. Non-writable unchanged branches may still pass
review; readiness retains all required CI, review and merge gates.

## Publication and recovery

Use create-pr's private preparation owner for checkout, safe conflict repair,
validation, commits and explicit non-force publication to the original branch.
Recheck permissions and exact identity immediately before publication; the
preflight is not an authorization receipt or cached grant. Renew complete-diff
review and CI evidence after head/base changes. Preserve caller files and refs.

If publication fails, distinguish proven permission denial, protection failure,
changed identity/head and unknown outcome using available evidence. A generic
error does not prove denial. Keep the existing owner journal and independently
reconcile the remote state before retry; report an unclassified rejection or
unconfirmed publication honestly. No speculative push, force, rebase, alternate
credential or replacement PR bypass is allowed.

## Official behavior

- [Maintainer edits on fork PR branches](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/allowing-changes-to-a-pull-request-branch-created-from-a-fork)
  defines the upstream-maintainer permission route.
- [Actions token scope](https://docs.github.com/en/actions/concepts/security/github_token)
  confines the built-in installation token to the workflow repository.
- [GitHub CLI authentication environment](https://cli.github.com/manual/gh_help_environment)
  permits authentication tokens in either `GH_TOKEN` or `GITHUB_TOKEN`.
