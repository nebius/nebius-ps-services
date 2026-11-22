# Issue assessment status, types and findings

Research every selected issue using [research coverage](research.md), then
classify its material claims. Repository policy and accepted maintainer intent
supply supported contracts; contributor requests do not create new promises.
Separate the problem from its proposed fix. Missing code may reveal a valid
request or contract gap; it does not establish invalidity.

## Review status

Record `reviewed` after a substantive assessment, including an assessment that
identifies a precise missing fact. It means assessed, never accepted for design,
implementation or delivery, and does not certify feasibility. Do not add it for
unread, skipped or unassessed issues, or when required research could not be
performed. Explain that evidence limit instead. Existing labels do not prove a
fresh review; reassess current evidence before requested effects.

## Type

| Type | Required evidence |
| --- | --- |
| `bug` | An implemented supported path behaves incorrectly against its accepted contract. |
| `gap` | A capability or path promised by an accepted contract is absent or incomplete. |
| `feature-request` | A credible request introduces a capability outside the existing supported contract. |
| `enhancement` | The requested delta extends or improves an existing capability beyond its current contract. |
| `documentation` | Supported behavior needs missing, corrected or clearer documentation. |
| `question` | The request is for usage guidance, explanation or clarification. |

Feature existence alone does not establish enhancement: if the exact requested
behavior is already supported, record that outcome and its evidence. If a missing
part was promised, classify it as a gap rather than recasting it as a new feature.
Use bug for incorrect implemented behavior and gap for absent promised behavior.

For a mixed report, assess every material claim. Apply supported types for the
outstanding work and explain which portions are already covered. Multiple types
are permitted only for distinct evidenced claims, not competing guesses. An
uncertain type remains unset/pending; do not invent an `unknown` label. The
needs-information finding can coexist with reviewed without a type.

## Finding

Use at most one optional whole-issue finding, with per-claim exceptions explained
in the comment. Evidence-supported unresolved work needs only reviewed and its
supported types, with no extra finding label. These labels describe findings,
not GitHub state or delivery. Reviewed is independent of their mutual exclusion.
Review and create-plan retain observed state. Only the close action evaluates
this fresh assessment against [closure](closure.md); outcome labels alone never
change state or satisfy closure evidence.

| Finding | Required basis and next action |
| --- | --- |
| `needs-information` | A precise material evidence/product decision gap prevents a complete assessment. Ask the smallest useful question. |
| `already-supported` | Every requested material outcome is already supported in the applicable version; identify the verified usage path. |
| `already-fixed` | Every material defect/gap is resolved by an applicable verified revision/release; identify it and the proof. |
| `duplicate` | Every material claim is covered by a verified canonical issue; link it and explain equivalence. |
| `out-of-scope` | Evidence establishes a supported product boundary excluding the claim. A project-filter exclusion is not this outcome. |
| `deferred` | Explicit maintainer decision or accepted policy postpones valid work; record its basis and reconsideration conditions. |
| `not-planned` | Explicit maintainer decision or accepted policy excludes the work; record its basis. |

Whole-issue supported/resolved/duplicate labels require coverage of every
material claim. A fixed bug plus an unimplemented enhancement is not already
resolved: classify the enhancement and assess the remaining request. When
unresolved material uncertainty affects the whole decision, use needs-information.
Do not use association with an assessed issue to establish its siblings' findings.

Only an explicit maintainer decision or accepted policy establishes deferral or
not-planned status. Priority/cost analysis may recommend a decision but never
supplies that authority. Do not override an explicit maintainer disposition
without material new evidence
and authority to reconsider it. Age, rarity, popularity, wording, missing code,
failed reproduction or lack of response alone never justify rejection/deferral.
A passing current-version check does not disprove another supported-version bug.

## Importance is separate

Assess relevance, validity, severity, urgency, reach, confidence, workarounds,
effort, regression risk and ongoing support burden independently. Unknown reach
is not zero. Do not invent affected-user counts, deadlines, precise scores or
estimates. Rarity does not reduce severe irreversible harm.

Use the documented project severity scale. Otherwise rank critical (severe
loss/core failure), high (serious supported-workflow regression), normal
(meaningful defect/improvement), low (limited impact with adequate workaround),
then unknown. Sensitive security evidence follows the private route. Labels for
severity/priority require documented existing mappings; do not create them.
Project processing order and remediation order are defined in [grouping](batching.md).

## Optional planning eligibility

A review can finish without deciding implementation details. Reviewed means an
assessment was recorded, not that outstanding work is actionable or approved.

Only `--action create-plan` considers evidenced actionable, non-deferred issues whose
material product choices and dependencies are resolved. An unresolved design
choice can be settled by the requested design adviser within accepted intent;
a maintainer product decision must not be invented. Keep planning pending when
material choices remain. Reviewed or type labels alone never establish planning
eligibility. Supported/resolved/duplicate/rejected/uncertain findings
remain contextual references, not plan members. Do not plan new implementation
for selected closed issues or reopen them; report the need for separate handling.

Compatible eligible issues can share one plan, retaining individual acceptance
criteria. Relatedness alone is insufficient. A saved plan is a complete proposed
handoff, not authority to implement, close an issue or promise a date.
