# AI Stack Selection Framework

Use this framework for every non-trivial stack decision. It converts a product
comparison into a requirements, evidence, and ownership decision.

## Contents

- Decision order
- Hard gates
- Candidate scorecard
- Status and evidence rules
- Comparison procedure
- Decision record template

## Decision Order

Evaluate in this order. A later advantage cannot compensate for a failed earlier
gate unless the user explicitly changes the requirement.

1. Task fit: Can the option satisfy the required behavior and modality?
2. Legal and data fit: Are license, provenance, residency, privacy, retention,
   and usage terms acceptable?
3. Safety and trust fit: Can access, tool authority, isolation, approvals, and
   auditability be enforced outside model discretion?
4. Compatibility fit: Does the option work with the required model, hardware,
   interfaces, formats, protocols, and surrounding system?
5. Quality fit: Does representative evaluation meet the acceptance threshold?
6. Reliability fit: Are retries, recovery, degradation, upgrades, and rollback
   bounded and testable?
7. Operational fit: Can the team deploy, observe, secure, recover, and maintain
   it within the ownership model?
8. Performance and cost fit: Does controlled measurement meet latency,
   throughput, resource, and cost targets?
9. Reversibility and ecosystem fit: Can the choice be changed without an
   unjustified migration burden?

Start with these baselines:

- Use the current component or no new component.
- Use a direct provider or framework API before adding orchestration.
- Use source-native data access before adding a new index or database.
- Use one process or framework-native distribution before adding a separate
  control plane.
- Use documented defaults before optimization flags.

## Hard Gates

Reject or block a candidate when any required gate fails:

- Required model, modality, license, region, hardware, or API support is absent.
- Model or data terms conflict with the intended use or distribution.
- Tenant, document, or tool authorization cannot be enforced at a trusted
  boundary.
- Required data deletion, retention, lineage, or audit behavior is unavailable.
- A required compatibility edge is unknown and no bounded validation is
  possible.
- Representative quality or safety evaluation fails the approved threshold.
- Failure, recovery, upgrade, or rollback behavior is unacceptable for the
  workload impact.
- The candidate has no operational owner or exceeds the accepted ownership
  burden.

Do not average hard-gate failures into a numeric score.

## Candidate Scorecard

Score only candidates that pass all hard gates. Prefer qualitative ratings with
specific evidence over false numerical precision.

| Dimension | Questions | Typical evidence |
| --- | --- | --- |
| Functional fit | Does it support the exact workload and interfaces? | Official docs, minimal integration test |
| Quality | Does it meet task-specific acceptance targets? | Representative evaluation set |
| Latency and throughput | Does it meet percentile and saturation targets? | Controlled benchmark |
| Resource efficiency | What accelerator, CPU, memory, storage, and network are required? | Profiles and capacity tests |
| Cost | What is the cost per accepted outcome at target load? | Measured usage and current pricing |
| Reliability | How does it handle failure, retry, recovery, upgrade, and rollback? | Fault tests and runbooks |
| Security and privacy | Can trust, identity, authorization, isolation, and retention be enforced? | Architecture and security tests |
| Operability | Can the team observe, debug, scale, patch, and recover it? | Operational trial and owner review |
| Portability | Which models, formats, providers, protocols, and platforms are portable? | Compatibility tests and export paths |
| Maturity | Is the required feature stable, supported, and maintained? | Official lifecycle and release documentation |
| Integration cost | What new code, state, infrastructure, and migration are required? | Implementation estimate and prototype |
| Reversibility | What is the exit path and retained artifact format? | Migration or rollback exercise |

Record both benefits and new failure modes. A feature that adds throughput but
also adds distributed state, cache coherence, or routing complexity must carry
that operational cost.

## Status And Evidence Rules

Assign exactly one component status:

- `Required`: A current requirement cannot be met without it.
- `Conditional`: A named, observable trigger may make it required.
- `Deferred`: It could help later, but no current trigger is met.
- `Rejected`: It fails a gate or is dominated by a simpler choice.

Assign every material claim one evidence state:

- `Measured`: Reproduced on the target or representative controlled setup.
- `Officially documented`: Supported by current primary documentation.
- `Assumed`: Not yet verified and cannot be used to close a hard gate.

Use evidence at the correct level:

- A feature list proves documented availability, not target compatibility.
- A vendor benchmark proves behavior on its published setup, not the target
  setup.
- A passing smoke test proves basic integration, not quality, scale, recovery,
  or production readiness.
- A single average hides tail latency, variance, saturation, and failure modes.

## Comparison Procedure

1. Freeze the workload contract and acceptance gates.
2. Record the baseline, including the option to add nothing.
3. Eliminate candidates that fail hard gates.
4. Verify volatile facts from official sources.
5. Build the compatibility graph for remaining candidates.
6. Prototype the narrowest decision-changing risky or unknown edge, not the
   largest system.
7. Benchmark with identical models, data, prompts, hardware, concurrency, and
   software settings where comparison requires them.
8. Evaluate quality and safety before optimizing throughput.
9. Compare total ownership and cost per accepted outcome, not only raw request
   cost or tokens per second.
10. Select the simplest sufficient candidate that passes all gates.
11. Record rejected options and the evidence that could reopen them.

## Decision Record Template

```markdown
### <capability>

Workload: <contract identifier>
Decision: <technology or pattern>
Status: Required | Conditional | Deferred | Rejected
Evidence: Measured | Officially documented | Assumed

Requirement served:
- <specific requirement>

Why this is the simplest sufficient choice:
- <reason>

Compatibility dependencies:
- <edge and verification state>

Acceptance gate:
- <metric, threshold, dataset/workload, and environment>

New failure modes and controls:
- <failure mode>: <control>

Owner:
- <team or role>

Revisit trigger:
- <observable condition>

Rejected alternatives:
- <alternative>: <reason and evidence>
```
