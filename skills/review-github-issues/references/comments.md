# Concise issue comments

Every comment contains an issue-specific decision, rationale supported by the
reviewed evidence, and a useful next step. Use only the rows that apply; no
boilerplate checklist, full implementation plan, raw logs or delivery promise.

| Disposition | Comment content | State |
| --- | --- | --- |
| Accepted | Supported problem, why this batch covers it, repository-relative plan path explicitly described as saved locally until published | Open |
| Needs information | Missing evidence and the smallest concrete question or reproduction detail needed | Open |
| Deferred | Validity, current priority rationale and reconsideration condition | Open |
| Duplicate | Verified canonical issue link and why the same problem/acceptance is covered | Close with supported duplicate relationship |
| Not planned | Specific scope or disproving evidence and courteous explanation | Close as not planned |
| Already resolved | Verified fixing revision/version and relevant validation or supported-source evidence | Close as completed |

Only post acceptance after the complete saved plan and dependent member/source
evidence have been independently verified. A repository-relative path is a
locator, not a hosted link; never expose an absolute local path. Use a verified
repository link only once that file actually exists at the referenced revision.

Apply the exact author/issue/decision/evidence deduplication and independent
readback in [GitHub operations](github-operations.md). A new meaningful decision
gets one concise explanation; unchanged evidence gets no repeated comment.
Do not edit another author's comment or treat copied markers as effect receipts.

After implementation, the authorized PR owner uses the separate
[issue resolution handoff](../../worktree/references/issue-resolution.md) for
an implementation/validation summary and native closing references. This review
skill does not implement, create a PR, merge or post speculative completion.
