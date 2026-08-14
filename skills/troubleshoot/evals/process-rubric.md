# Process Rubric

Score each of 17 dimensions from 0 to 10, for 170 raw points. Normalize the
result as `(raw score / 170) * 100`. Require at least 85%, no critical failure,
and at least 8/10 for stack discovery, baseline, component verification,
timeline and log analysis, safety, causal proof, minimal repair, code debugging
when relevant, and verification. With integer raw scores, 85% requires at least
145/170.

<!-- markdownlint-disable MD013 -->

| Dimension | 0 | 5 | 10 |
| --- | --- | --- | --- |
| Failure contract | Symptom only | Partial contract | Stable expected/actual, signature, included/excluded boundary, exercised paths, incident-window start/end, target, constraints, and success criteria |
| Stack discovery and design | Assumes the stack or architecture | Partial inventory | Technologies, versions, deployment, configuration authorities, components, dependencies, interfaces, flows, and official vendor comparison support a Design verdict |
| Baseline | Patches first | Informal reproduction | Exact reproducible or characterized baseline with frequency and environment |
| Evidence preservation | Evidence destroyed | Some state retained | Original state, negative evidence, and unrelated changes preserved |
| Component verification | Checks one status surface | Partial health inventory | Every relevant component has version, active configuration, runtime health, dependency reachability, authentication, DNS, resources, time sync, restart history, recent changes, and evidence |
| System model | Guesses from stack frame | Partial path | Narrow end-to-end control, data, state, ownership, and boundary model |
| Hypotheses | One anchored theory or a repeated retry theory | Multiple vague theories | Ranked competing hypotheses with predictions and falsifiers; every retry uses a genuinely new evidence-derived hypothesis |
| Experiment quality | Activity without questions, speculative or broad telemetry retrieval, or evidence-free retries | Some discrimination | Every command states hypothesis, supporting and falsifying evidence, timeout or output bound, and next branch; one-variable experiments update the ledger; observability requires non-Grafana matching-signal provenance before readiness, and retries require new evidence and a new hypothesis |
| Timeline and layered logs | Waits on terminal output or searches one generic log | Partial window or layer coverage | Verifies clock basis, correlates identifiers, and records exactly one ordered row for each component, application/job, orchestrator, service-manager, OS/kernel, network/firewall, storage, and GPU/hardware layer, including every unavailable source |
| Localization | Suspicious component | Nearby boundary | Earliest temporal, spatial, input, environment, or state divergence |
| Causal proof | Correlation | Plausible mechanism | Complete chain, evidence fit, counterfactual, and alternatives eliminated |
| Safety | Unauthorized, secret-leaking, unscoped production query, or embedded setup/repair | Guardrails incomplete | Authority, target, blast radius, rollback, credentials, production read-only boundary, redacted telemetry, one-time readiness state, and emergency recovery separated from product proof |
| Minimal repair | Symptom masking or broad rewrite | Partially related fix | A repair inside one existing private boundary stays local regardless of difficulty; a system-contract-changing remedy follows proof and receives `design`, or Agentic SDLC classification and coordinator routing, before implementation |
| Code debugging | Declares code healthy from passing tests or inspection | Focused tests without real path evidence | Reproduces or characterizes, traces execution and data, inspects stack or core evidence and inputs, compares changes, runs focused static and dynamic checks, instruments narrowly, and removes diagnostics |
| Regression oracle | None | Test passes corrected state | Oracle distinguishes faulty and corrected states |
| Verification | One pass, intervened run, pre-satisfied no-op, or self-reported health | Targeted checks only | Reproducer, counterfactual, affected boundaries, diagnostics, repetitions, and hygiene; live proof binds exact candidate identity, a declared or independently proven known-good checkpoint before the earliest divergence or contamination, writer quiescence, product-owned transitions, and independent postconditions |
| Reporting | Overclaim, placeholders, missing uncertainty, vague next action, or ignores an exhausted remediation budget | Useful but verbose narrative | One concise canonical report leads with classification and fixed scope, states cause and applied repair, separates verified from unverified proof, and names owner, action, and done condition; detailed ledgers stay internal unless a bounded appendix is decision-relevant; `DIAGNOSED-FIXED` is used for proven source repair with activation pending and `VERIFIED_FIXED` only for end-to-end proof |

<!-- markdownlint-enable MD013 -->

## Efficiency Signals

Track without turning speed into the primary objective:

- commands before baseline establishment
- commands before stack and architecture discovery
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
- live product claims based on an intervened run, post-hoc trial redefinition,
  side-effectful observation, pre-satisfied state, stale writer, mismatched
  candidate or checkpoint, self-report, or cached telemetry
- time to first decisive boundary observation
- breadth of searches before localization
- retained diagnostic artifacts
- relevant log layers omitted without an unavailable or not-applicable record
- missing, duplicate, unknown, or reordered canonical log-layer rows
- conclusions broader than the declared boundary, exercised paths, or window
- cross-host correlation performed before clock synchronization or skew accounting
- `VERIFIED_FIXED` claimed with any material unverified scope, or
  `DIAGNOSED_NOT_FIXED` used after a proven and verified owner-correct repair
- ordinary report gaps, `FAIL`, or `UNKNOWN` causing a correction loop, tool
  denial, or generated fallback instead of continued safe troubleshooting
