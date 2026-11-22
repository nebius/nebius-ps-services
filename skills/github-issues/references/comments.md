# Concise review comments

When comments are authorized under the core Invocation Policy, publish one
concise issue-specific finding after research, independently of any
requested planning. Include assessment status, supported type/finding, decisive evidence and
confidence/gaps, plus exact related-issue links and the relationship rationale
when relevant. Avoid boilerplate checklists, raw logs, implementation steps and
delivery promises. Preserve disclosure boundaries for source/issue links.
Otherwise report findings in chat and skip publication, including plan-ready
comments; do not treat an unrequested comment as a failed or pending effect.

| Finding | Comment content |
| --- | --- |
| Unresolved work | Supported unresolved problem and evidence; no implementation acceptance promise. |
| Needs information | Exact missing version, reproduction, contract or product decision. |
| Already supported | Applicable supported usage path covering every material requested outcome. |
| Already fixed | Applicable verified revision/release and coverage of every material defect/gap. |
| Duplicate | Verified canonical issue link and why all material claims are covered. |
| Deferred | Explicit maintainer decision or accepted policy and reconsideration condition. |
| Out of scope / not planned | Specific supported boundary or explicit maintainer decision/accepted policy. |

Review and create-plan retain observed issue state. Close separately follows
[closure](closure.md), including verified explanation before mutation. Use plain findings, not bot
commands or automatic closing directives. Follow [labels](labels.md) for known
comment-triggered automation. Mixed reports explain covered portions separately
and assess outstanding work without claiming the entire issue is resolved.

Default review does not mention a fabricated plan path, promise to implement or
offer implementation approval. Existing plans do not authorize new planning.
Only `--action create-plan` may later add a concise, independently verified plan-ready
locator. Describe a repository-relative path as saved locally until it is
actually published; never expose absolute private paths or invent a hosted link.
A failed save leaves planning pending without blocking the review finding.

Apply acting-author/issue/evidence deduplication and independent readback from
[operations](github-operations.md). Reuse equivalent findings on unchanged
evidence; use one new explanation for a materially changed assessment or newly
verified requested-plan readiness. Never edit another author's comment or treat
copied markers as receipts. Do not repeat comments merely to restate labels.

After separately authorized implementation, the PR owner uses the independent
[issue resolution handoff](../../worktree/references/issue-resolution.md).
This skill never implements, creates PRs or merges. Standalone close is a
separate explicitly selected action; saved-plan approval never enables it.
