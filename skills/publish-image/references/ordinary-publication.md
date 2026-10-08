# Ordinary publication

Use create-pr's [ordinary completion](../../create-pr/references/ordinary-completion.md)
and [commit continuation](../../create-pr/references/commit-continuation.md).
The public setup/prep/publish/complete modes remain orchestration modes. The
shell helper's private modes are prep/tag/push/verify; it is not a second Git
workflow. Public publish resumes the already-reviewed, actually merged result.

Prep changes release content only on a selected non-default branch. Reuse the
existing branch, or begin canonical intake then create a feature from default.
Preserve the caller's grant through safe review fixes. Complete invokes local
review-pr and merge-pr; prep returns the reviewed PR without merging. Missing
Actions configuration stops; never use raw commits or merge commands as fallback.

After merge-pr proves the resulting SHA in remote default history and passing
applicable result CI, freeze repository/origin, PR, reviewed head/base, result,
tag/version, workflow and destination in private task state. Create a clean
isolated clone at that exact result; do not switch the user's worktree. Retain
existing credential/signing stores without copying secrets.

```text
bash <skill>/scripts/publish-KIND-doer.sh --mode tag <project-options> \
  --tag <version> --release-commit <verified-result-sha>
bash <skill>/scripts/publish-KIND-doer.sh --mode push <project-options> \
  --tag <version> --release-commit <verified-result-sha> --tag-object <recorded-oid> --origin-digest <frozen-origin-sha256>
```

Replace KIND with this skill's artifact type. Project options are those shown
by the helper's help, including prefix, project/changelog and chart inputs.
Freeze the SHA-256 of JSON `[fetch_urls, push_urls]` from the effective origin
URLs at intake, using the same serialization as publish-release. Refuse multiple
or different destinations and credentials in URLs. Reverify the isolated clone
uses those frozen destinations. Record the annotated tag object before push. Push verifies
object, peeled commit, remote ancestry and any existing tag; an exact remote
match is idempotent. A different tag blocks. If interrupted, inspect the surviving
clone and remote tag first. Never recreate a timestamp-different tag or repeat
publication blindly. Lost unrecorded local tag evidence requires reconciliation.

Observe the workflow identified by tag, result and configured workflow filename.
Existing publication environment approvals remain authoritative. Verify the exact
image tag/digest or OCI chart pull/version/digest and report source, workflow and
registry evidence separately. A green workflow alone is not registry proof.
