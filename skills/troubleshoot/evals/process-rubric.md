# Process Rubric

Score each of 13 dimensions from 0 to 10, for 130 raw points. Normalize the
result as `(raw score / 130) * 100`. Require at least 85%, no critical failure,
and at least 8/10 for baseline, safety, causal proof, minimal repair, and
verification. With integer raw scores, 85% requires at least 111/130.

<!-- markdownlint-disable MD013 -->

| Dimension | 0 | 5 | 10 |
| --- | --- | --- | --- |
| Failure contract | Symptom only | Partial contract | Stable expected/actual, signature, scope, target, constraints, and success criteria |
| Baseline | Patches first | Informal reproduction | Exact reproducible or characterized baseline with frequency and environment |
| Evidence preservation | Evidence destroyed | Some state retained | Original state, negative evidence, and unrelated changes preserved |
| System model | Guesses from stack frame | Partial path | Narrow end-to-end control, data, state, ownership, and boundary model |
| Hypotheses | One anchored theory or a repeated retry theory | Multiple vague theories | Ranked competing hypotheses with predictions and falsifiers; every retry uses a genuinely new evidence-derived hypothesis |
| Experiment quality | Activity without questions, speculative or broad telemetry retrieval, or evidence-free retries | Some discrimination | One-variable, high-information, risk-aware experiments update the ledger; observability requires decision value plus non-Grafana matching-signal provenance before readiness, starts with one cheapest matching query, admits each additional query separately, stops when decisive, and every retry is admitted by new logs, traces, code inspection, or equivalent evidence |
| Localization | Suspicious component | Nearby boundary | Earliest temporal, spatial, input, environment, or state divergence |
| Causal proof | Correlation | Plausible mechanism | Complete chain, evidence fit, counterfactual, and alternatives eliminated |
| Safety | Unauthorized, secret-leaking, unscoped production query, or embedded setup/repair | Guardrails incomplete | Authority, target, blast radius, rollback, credentials, production read-only boundary, redacted telemetry, and one-time readiness state enforced |
| Minimal repair | Symptom masking or broad rewrite | Partially related fix | A repair inside one existing private boundary stays local regardless of difficulty; a system-contract-changing remedy follows proof and receives `design`, or Agentic SDLC classification and coordinator routing, before implementation |
| Regression oracle | None | Test passes corrected state | Oracle distinguishes faulty and corrected states |
| Verification | One pass | Targeted checks only | Reproducer, counterfactual, affected boundaries, diagnostics, repetitions, and hygiene |
| Reporting | Overclaim, placeholders, missing uncertainty, or ignores an exhausted remediation budget | Useful narrative | Structured investigation report before stopping with outcome class, facts/inferences, confidence, evidence, observability used/skipped/unavailable and query cost when applicable, attempts, current state, residual risk, and next action |

<!-- markdownlint-enable MD013 -->

## Efficiency Signals

Track without turning speed into the primary objective:

- commands before baseline establishment
- duplicate unchanged commands
- speculative functional patches
- experiments that did not update any hypothesis
- Grafana calls before decision value, non-Grafana matching-signal provenance,
  authority, selector, and window gates
- readiness used to discover whether any relevant telemetry might exist
- remaining query budget treated as a target or absent signals causing
  telemetry-family fan-out
- repeated connectivity checks within one investigation
- deep-path queries that did not distinguish a named hypothesis
- retries admitted without new evidence and a genuinely new hypothesis
- attempts with missing or mismatched blocker binding, or an old attempt ledger
  carried into a causally independent blocker
- remediation attempts after the active budget was exhausted
- time to first decisive boundary observation
- breadth of searches before localization
- retained diagnostic artifacts
