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
| Hypotheses | One anchored theory | Multiple vague theories | Ranked competing hypotheses with predictions and falsifiers |
| Experiment quality | Activity without questions | Some discrimination | One-variable, high-information, risk-aware experiments update the ledger |
| Localization | Suspicious component | Nearby boundary | Earliest temporal, spatial, input, environment, or state divergence |
| Causal proof | Correlation | Plausible mechanism | Complete chain, evidence fit, counterfactual, and alternatives eliminated |
| Safety | Unauthorized or secret-leaking | Guardrails incomplete | Authority, target, blast radius, rollback, credentials, and production boundary enforced |
| Minimal repair | Symptom masking or broad rewrite | Partially related fix | Narrow invariant-restoring repair directly follows from cause |
| Regression oracle | None | Test passes corrected state | Oracle distinguishes faulty and corrected states |
| Verification | One pass | Targeted checks only | Reproducer, counterfactual, affected boundaries, diagnostics, repetitions, and hygiene |
| Reporting | Overclaim or missing uncertainty | Useful narrative | Outcome class, facts/inferences, confidence, evidence, changes, residual risk, and next action |

<!-- markdownlint-enable MD013 -->

## Efficiency Signals

Track without turning speed into the primary objective:

- commands before baseline establishment
- duplicate unchanged commands
- speculative functional patches
- experiments that did not update any hypothesis
- time to first decisive boundary observation
- breadth of searches before localization
- retained diagnostic artifacts
