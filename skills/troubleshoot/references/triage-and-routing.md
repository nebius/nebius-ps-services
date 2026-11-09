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

## Sources

- [Google SRE troubleshooting](https://sre.google/sre-book/effective-troubleshooting/)
- [USE methodology](https://www.brendangregg.com/usemethod.html)
