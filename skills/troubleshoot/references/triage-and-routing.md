# Triage And Routing

At intake, define expected versus actual behavior, target/artifact, incident
window, impact, prior interventions, authority and the independent success oracle.
Choose an initial boundary from evidence. A route is provisional, never an exclusion.

| Route | Starting question | Evidence that changes the route |
| --- | --- | --- |
| Code | Where does the executed path first transform correct input/state incorrectly? | Correct application output reaches a failing platform boundary. |
| Infrastructure | Where does the actual workload resource or service path diverge? | Platform delivers the required contract but application handling fails. |
| Mixed | Which application/dependency boundary first disagrees? | Scoped comparison isolates one owner or demonstrates multiple contributors. |
| Undetermined | What smallest observation distinguishes application from immediate platform dependency? | Artifact, input/output or resource evidence supports a narrower route. |

Configuration, compatibility, permissions and resource constraints are hypotheses
across every route. A timeout is not a network diagnosis; high CPU is not a capacity
diagnosis; a passing unit test does not exclude an unexercised deployed path.

Map only the failing request, job or state transition. Select the starting layer
and direction from observed evidence; compare values at component boundaries and
bisect when serial traversal becomes expensive. Keep affected/unaffected inputs,
configuration, artifacts and observation windows comparable. See
[technique selection](technique-selection.md) for reliable tests and skipped revisions.

Apply utilization, saturation and errors (USE) only to implicated compute, storage
and network resources. Combine these with request latency/errors and application
correctness; USE alone does not diagnose configuration or logical defects. Measure
the workload's actual cgroup, namespace, mount, device and service boundary, not
just host averages. New evidence can move code → infrastructure or the reverse
without resetting the failure contract or remediation budget.

Load only relevant technology guides. Each guide specifies applicability, version
fingerprint, symptom, alternatives, cheapest distinguishing evidence, interpretation,
next branch, diagnostic effects and verification. Verify command syntax against
the observed version. Undocumented or unsupported combinations remain explicit gaps.

## Select A Provisional Decision Approach

Keep three questions separate: **where** the failure might live (technical route),
**how** to learn the next useful fact (approach), and **how urgently** impact needs
containment. Use the existing MODEL/HYPOTHESES/EXPERIMENTS loop, not new states or
a mandatory classification exercise. A mixed incident can contain several kinds
of uncertainty; do not label an entire cluster or VM permanently complex/chaotic.

| Evidence context | Approach and next action | Proof boundary |
| --- | --- | --- |
| A recognizable signature with matching context | Prioritize the candidate and its cheapest discriminator | Recognition is not diagnosis; verify baseline, fit and counterevidence |
| A discoverable mechanism with useful alternatives | Analyze the failing path and compare distinguishing predictions | Preserve material alternatives and counterfactual proof |
| Interacting state makes prediction unreliable | Use a bounded, authorized experiment with observable branches | Bound effects; safe-to-fail does not mean authorized or consequence-free |
| Insufficient or conflicting context | Orient with the cheapest reliable discriminating observation | Unknown stays unknown; avoid forcing a domain label |

Cynefin informs provisional sensemaking; recognition-primed decisions inform
candidate ordering; bounded rationality limits search effort. Human expertise
claims do not transfer to model familiarity. These are unqualified efficiency
hypotheses until native comparisons show benefit without capability regression.

If an incident needs immediate stabilization, preserve volatile evidence when
possible and use only the exact containment action already authorized. Urgency
can coexist with a known clear cause. Record stabilization separately from repair
and proof; a restart does not establish the mechanism or clean replay. No domain
classification expands authority or permits an irreversible experiment.

Reconsider the approach when versions/configuration differ from the familiar
pattern, evidence contradicts predictions, a boundary is crossed, or interactions
invalidate a simple mechanism. Reorientation changes the next question, never the
failure contract, attempts, active time or intervention history. Preserve exclusion
history while reopening every conclusion invalidated by the new evidence.

## Sources

- [Google SRE troubleshooting](https://sre.google/sre-book/effective-troubleshooting/)
- [USE methodology](https://www.brendangregg.com/usemethod.html)
- [Cynefin authors: decision approaches](https://thecynefin.co/effective-decision-making-support-tool)
- [Klein: recognition-primed decisions](https://www.gary-klein.com/rpd)
- [Kahneman and Klein: expertise limits](http://www.hansfagt.dk/Kahneman_and_Klein(2009).pdf)
- [Simon: bounded rationality](https://www.nobelprize.org/uploads/2018/06/simon-lecture.pdf)
