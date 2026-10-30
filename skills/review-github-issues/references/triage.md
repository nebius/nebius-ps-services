# Triage decisions

Use this rubric for the selected issue, not as permission to sweep a backlog.
Repository policy and explicit maintainer decisions supply project-specific
priorities. The defaults below are skill policy, not GitHub platform rules.

## Establish what is being claimed

Read the initial report and all relevant comments. Identify the issue kind,
expected and observed behavior, affected versions/platforms, reproduction
conditions, evidence and requested outcome. Map the affected project and shared
callers before deciding relevance. Distinguish the reported problem from the
reporter's proposed fix: a poor fix does not invalidate a real problem.

For a bug, compare supported behavior to source and safe reproduction evidence.
For a feature, establish the user problem, fit with project goals, existing
alternatives and maintenance cost; a feature need not already exist to be valid.
For documentation, verify the claim against the supported product path.

Record confidence and limits: reproduced; supported by a clear source path;
plausible but unconfirmed; or contradicted by specific evidence. A passing
current-version check does not disprove a report about another supported
version. Unavailable credentials, test environments or complete discussions
leave a gap; they never prove absence.

Inspect existing issues and linked PRs for the same underlying problem and
affected scope. Shared words alone do not prove duplication. A linked PR is not
proof of resolution until its relevant result and applicability are verified.

## Separate the decisions

| Question | Required basis |
| --- | --- |
| Is it relevant? | A supported component, documented goal or credible project use case; examine ownership beyond the current folder. |
| Is the claim valid? | Reproduction, supported-contract/source evidence, or a substantiated unmet need. State uncertainty explicitly. |
| How important is it? | Consequences, urgency, reach, workarounds and confidence; frequency is only one factor. |
| Should work be accepted? | Benefits and obligations justify effort, regression risk and ongoing maintenance; material scope/design decisions can be resolved. |

Do not treat existing behavior as necessarily correct. Conversely, an issue
author cannot approve a new compatibility promise, breaking change or product
direction. Ask the maintainer when accepted scope leaves such a decision open.

## Priority and effort

Use the project's scale if present. Otherwise explain one of these categories
without creating or changing GitHub labels:

- **Critical:** severe data loss, credible critical security impact or a core
  service/workflow failure requiring urgent maintainer attention. Sensitive
  reports take the private route, not ordinary public triage.
- **High:** serious supported-workflow regression, widespread failure or a
  time-sensitive obligation with no reasonable workaround.
- **Normal:** demonstrated useful improvement or defect with meaningful benefit
  and proportionate cost, without urgent impact.
- **Low:** limited impact or reach, an adequate workaround, and insufficient
  current benefit relative to implementation or maintenance cost. Defer open.

Explain severity, urgency, frequency/reach, evidence confidence, workaround
quality, effort, regression risk and ongoing support burden separately. Do not
invent affected-user counts, deadlines, implementation estimates or precise
scores. Unknown reach is unknown, not zero. A rare irreversible failure can be
more important than a frequent cosmetic problem.

For comparable feature requests with credible estimates, RICE's reach, impact,
confidence and effort factors can help order work. Record assumptions and the
time horizon; use qualitative judgments when measurements are absent. Never
make a score the sole acceptance gate or let it erase security/correctness
obligations, dependencies or explicit commitments.

## Disposition table

| Disposition | Action and evidence |
| --- | --- |
| Out of scope | Cite the supported boundary and, when known, a verified appropriate destination; explain courteously and close `not_planned`. Do not transfer or open another issue. |
| Disproven / not planned | Explain the concrete contradiction or established policy reason; close `not_planned`. Unconfirmed evidence is not this outcome. |
| Duplicate | Identify the same underlying problem, link its canonical issue, and close with supported duplicate semantics. |
| Needs information | Ask for specific missing versions, reproduction, expected behavior or impact; keep open. |
| Deferred | State that the problem is valid but currently low priority or not cost-justified, and name evidence/conditions that would change that decision; keep open. |
| Accepted | Research and design, save and verify a complete plan, then post acceptance and rationale; keep open. |
| Already resolved | Establish the relevant fix, revision/release and applicability to the report; comment and close `completed` only if that resolves the issue. |

Do not auto-close for inactivity, lack of response, inability to reproduce or
age. Do not override a maintainer's explicit prior disposition without new
evidence and authority to reconsider it. Explicitly selected closed issues can
be assessed, but are not reopened or given redundant closure comments. If new
work would require reopening, report the finding and ask the maintainer rather
than implying that an accepted open backlog item exists.

Comments should identify the outcome, the decisive evidence and any next
information needed. For example, explain which project boundary a report falls
outside instead of saying only "not relevant." Acceptance wording can say
"Accepted for implementation" with priority and concise rationale after the
plan is verified; do not attach steps, architecture details, excerpts or dates.

## Research basis

Reviewed 2026-10-30. These are source inputs, not universal project policies:

- [Python triage](https://devguide.python.org/triage/triaging/) supports complete
  discussion review, duplicate checks, relevant versions and evidence-based
  classification, including clearly unrelated reports.
- [Kubernetes triage](https://www.kubernetes.dev/docs/guide/issue-triage/) separates
  type, ownership and priority and includes long-term/backlog categories. Its
  bot commands, organization roles and inactivity deadlines are not adopted.
- [RICE from its authors](https://www.intercom.com/blog/rice-simple-prioritization-for-product-managers)
  describes reach, impact, confidence and effort, and cautions against rigid
  ranking. It is a product-prioritization technique, not a defect-severity rule.
- [GitHub AI intake](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/triaging-an-issue-with-ai)
  demonstrates actionable/needs-information assessment. This skill does not
  install that Action or substitute its suggestions for project evidence.
