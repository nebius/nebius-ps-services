# Evaluation, Observability, and Governance

Use this reference to make an agent architecture releasable, observable,
auditable, and recoverable throughout its lifecycle.

## Contents

- Evaluation specification and layers
- Outcome verification and revision
- Dataset and grader policy
- Trace and privacy model
- Release identity and promotion
- Ownership and change management
- Incident response
- Readiness checklist

## Evaluation Specification

Define evaluation before optimizing architecture or autonomy:

```text
capability and workload contract
representative task set and traffic distribution
baseline and alternatives
metrics, thresholds, confidence, and regression budget
required slices and unacceptable outcomes
trial count and nondeterminism policy
fixtures, simulators, graders, and human review
exact release identity under test
owner, cadence, promotion, rollback, and failure disposition
```

Measure cost per accepted outcome, not just token price or raw completion rate.
Keep deterministic checks for mechanical properties and reserve model or human
judgment for qualities that cannot be reliably encoded.

Define an outcome contract with:

```text
observable success and unacceptable outcomes
required artifacts and evidence links
deterministic checks and authoritative read-backs
semantic quality rubric and independent reviewer when needed
failure, abstention, timeout, and escalation conditions
maximum revision iterations, time, and cost
owner and final decision authority
```

## Evaluation Layers

Use only applicable layers, but do not substitute one for another:

1. **Contract and unit**: schemas, prompt assembly, policy, budgets, state
   transitions, error mapping, and deterministic validators.
2. **Tool**: tool selection, argument correctness, authorization, refusal,
   idempotency, postcondition verification, and malformed outputs.
3. **Retrieval/context**: authorization, recall, precision, freshness,
   provenance, conflict handling, groundedness, citation, and abstention.
4. **Model/capability**: task quality, structured output, safety, calibration,
   latency, and cost under representative inputs.
5. **Trajectory**: plans, tool sequences, handoffs, retries, approvals, budget
   use, and policy compliance across the trace.
6. **End-to-end outcome**: user or business success and unacceptable effects.
7. **Adversarial/security**: direct and indirect injection, confused deputy,
   cross-tenant access, exfiltration, SSRF, unsafe tools, and sandbox
   assumptions.
8. **Resilience**: timeouts, rate limits, provider outage, duplicate delivery,
   crash/resume, cancellation, stale approval, partial effect, compensation,
   and reconciliation.
9. **Performance/economics**: latency distributions, throughput, saturation,
   resource use, fan-out, token use, and cost per accepted task.
10. **Online**: shadow/replay, canary, production guardrail metrics, drift,
    user feedback, and incident signals.

Evaluate the final outcome and the trajectory. A correct answer reached through
an unauthorized or fragile path is not an accepted result.

## Outcome Verification And Bounded Revision

Treat a model's completion claim as a request for verification. Prefer, in
order:

1. deterministic environmental checks such as authoritative read-back, tests,
   invariants, checksums, or policy results;
2. programmatic rubrics such as schemas, static analysis, required evidence,
   or numeric limits;
3. an independent model grader for semantic qualities that the first two
   levels cannot establish; and
4. human review for ambiguity, high impact, values, or business judgment.

When revision is allowed:

- use an independent context and a versioned task-specific rubric;
- cap iterations, wall time, and cost;
- give the verifier observable artifacts and results, not only the producing
  agent's explanation;
- never repeat a consequential side effect merely to improve a score;
- never allow a model grader to waive schema, policy, authorization, test,
  privacy, or safety failures;
- include anti-gaming cases and calibrate false acceptance and false rejection
  against expert-labelled examples; and
- stop and escalate when feedback is contradictory, non-actionable, or remains
  unsatisfied within the revision budget.

## Dataset Policy

- Version datasets and bind them to the release record.
- Keep representative normal, edge, rare, high-risk, and adversarial slices.
- Separate development, regression, hidden holdout, and production-feedback
  sets.
- Track provenance, license, consent, sensitivity, contamination, retention,
  deletion, and tenant boundaries.
- Preserve real workload distributions while protecting personal and
  confidential data.
- Add cases from incidents and observed failure classes only after safe
  sanitization and ownership review.
- Avoid optimizing repeatedly against a small visible benchmark.
- Use multiple trials for probabilistic behavior and report variance or
  confidence appropriate to the decision.

Synthetic data may expand rare-event coverage but does not prove real-world
quality. Require downstream comparison with a non-synthetic baseline.

## Grader Policy

Use deterministic graders for schemas, tool calls, state transitions,
authorizations, exact invariants, latency, and cost. Use model graders for
semantic qualities only when:

- the rubric is explicit and task-specific;
- outputs are blinded and order effects controlled;
- grader model, prompt, settings, and version are recorded;
- calibration against expert human labels is measured;
- disagreement and low-confidence cases route to human review;
- the grader cannot see secrets or unrestricted production content.

Do not use an LLM judge as the sole release authority for high-impact safety,
authorization, legal, or compliance outcomes.

## Trace Model

Correlate one user or system task across application, model, agent, tool,
workflow, data, and approval boundaries. Record bounded metadata such as:

```text
trace, run, task, attempt, and parent identifiers
tenant-safe principal and policy decision identifiers
release, model route, prompt, tool, data/index, and runtime versions
state transition, tool call, approval, effect, and verification events
latency, tokens, cost, retry, queue, and budget consumption
error class, terminal outcome, and evaluation linkage
```

Keep semantic-convention attribute details in the current observability stack
decision; do not freeze unstable vendor fields into this durable skill.

## Privacy and Audit

- Minimize captured prompts, outputs, retrieved text, tool payloads, and user
  content.
- Prefer identifiers, classifications, sizes, and digests when full content is
  unnecessary.
- Redact secrets and sensitive values before export.
- Apply tenant isolation, encryption, access controls, retention, deletion,
  residency, and purpose limitation to traces and eval datasets.
- Separate operational telemetry from immutable action/approval audit.
- Make sampling explicit and preserve required security and high-risk events.
- Review vendor tracing defaults and disable or replace exports that violate
  policy.
- Do not store hidden chain-of-thought. Store bounded rationale, evidence,
  decisions, and actions required for audit and debugging.

## Core Indicators

Select service-level indicators tied to the workload:

- accepted-task success and unacceptable-outcome rate;
- false-completion rate, where the agent claimed success but verification
  failed;
- tool selection, argument, authorization, and postcondition correctness;
- groundedness, citation, abstention, and stale/conflicting-context rate;
- approval request, rejection, expiry, and stale-replan rate;
- duplicate, retry, partial-effect, compensation, and reconciliation rate;
- p50/p95/p99 task latency and time in queue/approval/tool/model states;
- time to first useful verified progress and time to verified completion;
- step, token, cost, fan-out, and model-call distributions;
- provider/tool failure, circuit-open, saturation, and cancellation rate;
- human acceptance, correction, escalation, and incident rate.
- approvals per accepted task, rejection/expiry rate, and abandoned approval
  burden.

Alert on user-impact and unsafe trajectories, not only infrastructure health.

## Versioned Release Unit

Bind release evidence to the compatible identity of:

```text
application and domain contract
agent instructions and policy
model provider, endpoint, model, revision, and settings
runtime, adapters, and orchestration
tool schemas and implementations
skills, MCP servers, and remote-agent contracts
context assembly, retrieval data, embedding, index, and reranker
state schemas and migrations
evaluation datasets, graders, thresholds, and observability configuration
```

A model name alone is not a release. Rollback restores a complete compatible
identity or an explicitly tested alternative.

## Promotion Gates

Require, proportionately:

```text
contract/static -> offline quality and safety -> integration -> resilience
-> security -> performance/cost -> shadow or replay -> canary -> rollout
```

When releases must resume prior work, add a compatibility gate for supported
session state, checkpoints, event replay, effect receipts, tool contracts, and
memory snapshots. An incompatible release terminates or migrates explicitly;
it does not silently reinterpret durable state.

Block promotion when a required gate fails, evidence cannot be reproduced, the
release identity is ambiguous, authority depends on model behavior, recovery or
rollback is untested, or no owner accepts the operational burden.

Increase autonomy gradually:

1. deterministic or direct-call baseline;
2. governed read-only agent;
3. approval-gated reversible actions;
4. narrowly policy-granted low-risk actions;
5. multi-agent or remote expansion only after measured need.

Each rung needs its own acceptance and rollback gates.

## Ownership and Change Management

Assign owners for product outcome, application, model, data, retrieval,
security, privacy, platform, evaluation, and operations. One person or team may
hold several roles, but no critical boundary is ownerless.

For prompt, model, tool, policy, data, index, runtime, or topology changes,
record:

- change and rationale;
- affected workloads, risks, compatibility edges, and owners;
- evidence and assumptions;
- evaluation and rollout plan;
- rollback identity and switch condition.

Do not make silent prompt edits or unqualified provider fallback in production.

## Incident Response

Use this response flow:

```text
detect -> contain -> preserve evidence -> classify impact and authority
-> disable or degrade risky capabilities -> restore known-good identity
-> reconcile effects -> analyze cause -> add regression evidence -> re-release
```

Contain by disabling specific tools, routes, memory writes, or autonomy before
taking the entire product down when safe. Preserve privacy while retaining the
minimum evidence required for response. Do not replay an effect or repair state
until authoritative outcome and idempotency are known.

## Readiness Checklist

### Design

- Behavior classification and simplest baseline are explicit.
- Every probabilistic decision is bounded by typed input/output and policy.
- Tool, context, state, authority, failure, and ownership boundaries are clear.
- Multi-agent, memory, protocols, and infrastructure have current triggers.
- Evaluation and rollback are designed before implementation.

### Release

- Representative quality, safety, security, resilience, and performance gates
  pass for the exact release identity.
- Approvals, idempotency, cancellation, duplicate delivery, partial effects,
  reconciliation, and rollback are exercised.
- Traces, alerts, runbooks, owners, capacity, privacy, and incident controls are
  ready.
- Canary limits and stop conditions are explicit.

### Operation

- Drift, cost, latency, unsafe attempts, tool failures, stale context, and
  human correction are monitored.
- Incidents and production failures safely update eval coverage.
- Access, retention, models, tools, dependencies, policies, and rollback paths
  are reviewed on a defined cadence.
- Deferred and conditional components are revisited only when their observable
  triggers occur.

## Primary Sources

- [OpenAI agent evals](https://developers.openai.com/api/docs/guides/agent-evals)
- [Anthropic, Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
