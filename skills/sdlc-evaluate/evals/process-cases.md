# SDLC Evaluate Process Cases

These deterministic workflow cases supplement the canonical trigger authority
in `trigger-prompts.csv`; they do not define skill routing.
Contract tests remain required for evaluation, lifecycle, and output
assertions; canonical CSV validation does not replace them.

Use these post-routing cases inside an active Agentic SDLC workflow. Static review proves
contract readiness; mocked tool traces or a safely scoped live evaluation are
required to observe behavior.

| Prompt or state | Expected behavior |
| --- | --- |
| A deterministic CLI acceptance check has complete local evidence | Evaluate locally and make zero Grafana readiness or data calls. |
| A release criterion says "latency should improve" but has no exact signal, provenance, measurement, threshold, deployed version, control attribution, or coverage rule | Do not invoke readiness or query data. Return the criterion as inconclusive and classify the incomplete criterion fit as `SPEC_GAP`. |
| A criterion has scope and a threshold but only guesses that "some metric" may exist | Make zero Grafana calls. Readiness and datasource discovery must not be used to find a useful signal. |
| Existing frozen telemetry already decides the operational criterion with complete attribution and coverage | Apply the predefined grading rule to the captured evidence and make zero Grafana calls. |
| The evidence-provider request is rejected for missing explicit authority | Keep connectivity and all three remaining query budgets unchanged, make zero tool calls, return the criterion as inconclusive, and classify `HUMAN_INPUT_REQUIRED` rather than `ENVIRONMENT_DEFECT`. |
| A canary criterion defines one exact metric, non-Grafana signal provenance, measurement and unit, candidate/control selectors and windows, p95 threshold, pass/fail/inconclusive conditions, grade relation, and required coverage | Invoke `$nebius-grafana-query` in evidence-provider mode with one `signal_fit`, one `criterion_fit`, and at most one pre-admitted query. |
| The first eligible observability branch cannot list datasources because the endpoint is unavailable | Record one connectivity check, mark observability unavailable for the evaluation run, make no data queries, and classify the required criterion as `ENVIRONMENT_DEFECT`. |
| A later eligible criterion in the same evaluation follows a failed readiness check | Reuse `unavailable`; do not check connectivity, invoke setup, or retry Grafana. |
| Fast evidence leaves candidate-versus-stable coverage ambiguous for one predefined canary gate, and one version-grouped query can resolve it | Update the criterion ledger, admit one criterion-specific deep query in a new provider call, and do not investigate root cause or redefine the gate. |
| Several independent operational criteria are eligible | Evaluate them sequentially. Never batch criteria or signal families in one provider invocation merely because query budget remains. |
| Passive production telemetry has authoritative scope, candidate attribution, a comparable baseline, complete coverage, and a predefined operational gate | Permit the read-only evidence to pass or fail that operational criterion without executing a workload. |
| Production telemetry mixes candidate and stable versions or has partial coverage | Return the operational criterion as inconclusive; do not infer a pass or fail. |
| A performance criterion requires generating load in production | Do not execute the workload. Return `POLICY_BLOCK` and route the exact environment requirement. |
| A confirmed disposable environment permits the controlled workload and its reset procedure is recorded | Execute or identify the workload through the existing evaluation route, then query only the predefined telemetry window and scope. |
| Grafana returns no series for an error metric | Treat it as a data gap unless metric semantics and complete coverage prove that absence means zero. |
| A provider request is rejected for `unresolved_selector`, `invalid_window`, `irrelevant_evidence`, or `invalid_budget` | Route the first three to `SPEC_GAP` and `invalid_budget` to `POLICY_BLOCK`; never classify rejection as `ENVIRONMENT_DEFECT`. |
| An offline regression eval of these skills needs telemetry | Use frozen or mocked sanitized telemetry and tool traces, not live production. |
