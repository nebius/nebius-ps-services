# Trigger Prompts

Use these prompts to review metadata behavior. Static review establishes trigger
readiness only; fresh-session execution is required to observe activation.

## Should Trigger Implicitly

1. "This integration test fails only in CI about once in 100 runs. Two timeout
   increases and a retry did not solve it. Find and fix the actual cause."
2. "The service started returning corrupt payloads after yesterday's deploy,
   but only for one input shape. Diagnose and repair it."
3. "This Bash worker occasionally exits zero after a failed pipeline and leaves
   children running. Reproduce and fix it without adding sleeps."
4. "Latency p99 doubled while throughput stayed flat. Compare the last known
   good release and current release and prove the regression."
5. "The local Docker stack is healthy after a restart but fails again under
   load. Find the earliest failing boundary and make a durable non-production
   repair."
6. "The Kubernetes workload is Ready but requests intermittently time out from
   one caller. Treat the cluster as read-only and tell me what evidence proves
   the cause."
7. "The installed package works on x86_64 Linux but crashes on arm64 macOS with
   the same inputs. Identify the relevant environment difference."
8. "A production request intermittently fails across three services. Do not
   change production; correlate one failed request and specify the highest-value
   next experiment."
9. "Forty-three repair attempts have run for hours without resolving the same
   failure. Cap this blocker at five remediation attempts, require new evidence
   and a new hypothesis before each retry, explain the blocker, and wait for me
   before continuing."
10. "Five attempts exhausted a parser failure. A later assertion failure is
    causally independent. Prove the old attempts do not exhaust the new issue,
    start it at attempt 1, and require human input only after five attempts
    against that one assertion blocker."
11. "This cross-service failure has no proven cause yet. Investigate it first;
    if the durable remedy turns out to require a new service boundary or data
    owner, hand the proven diagnosis to the design workflow before anyone
    implements it."
12. "This production-only latency regression is scoped to service `<service>`
    in project `<project>` between these UTC timestamps. Use runtime evidence
    only if it can distinguish the current dependency and saturation
    hypotheses, and keep production read-only."
13. "$troubleshoot --attempt-limit=10 --time-limit-minutes=180 Diagnose this
    persistent cross-layer failure, preserve new evidence between repairs, and
    stop at the configured limit."

## Should Not Trigger Implicitly

1. "Fix this missing semicolon."
2. "Run the formatter and resolve lint warnings."
3. "Install the dependencies from the lockfile."
4. "Rename this class and update its imports."
5. "Review my local diff for maintainability issues."
6. "Implement the new account settings page."
7. "Update the expected snapshot because the approved copy changed."
8. "Create a Terraform module for this new network."
9. "Publish the next release."
10. "Explain how this framework's router works."
11. "The root cause is already proven. Design the replacement service boundary
    and give me a /plan handoff; do not re-run the investigation."

## Boundary Cases

- A deterministic test failure with an obvious incorrect expected value should
  stay with ordinary implementation.
- Repeated failed attempts, multiple symptoms, or an unclear cross-layer cause
  should activate troubleshooting even when the first symptom is a test failure.
- A first failed remediation should activate the bounded remediation contract
  before a second repair, even when the task began as ordinary implementation.
- Optional budget flags are parsed only after an exact leading `$troubleshoot`;
  quoted, fenced, embedded, malformed, or problem-trailing lookalikes do not
  authorize a profile change.
- A known Terraform, Helm, or cloud implementation should use the domain skill;
  an unexplained failure in the deployed stack should use troubleshooting and
  consult the domain skill for product-specific commands.
- A dedicated security scan should use `apply-security`; an authentication or
  authorization failure with an unknown causal mechanism may use troubleshooting
  while applying security guardrails.
- An unknown cause starts in `troubleshoot`. A proven cause whose requested
  output is only a boundary-changing solution design and `/plan` handoff starts
  in `design`. A combined solve request stays in `troubleshoot` through
  `PROVEN`, then hands off only a system-contract-changing remediation.
- A complex or large repair inside one existing private boundary remains in
  `troubleshoot`. In active Agentic SDLC, a proven design defect goes first to
  `sdlc-classify-failure`, not directly to a design or plan phase.
- A deterministic lint, compile, or isolated unit-test failure makes zero
  Grafana readiness or data calls. A deployed-runtime symptom still makes zero
  calls until a decision-changing evidence question, non-Grafana provenance for
  one matching signal, authority, a deterministic selector, and an absolute
  window exist. Grafana readiness must not be used to discover whether useful
  telemetry might exist.
- An eligible runtime investigation uses one lazy Grafana readiness check for
  the investigation, then starts with one cheapest matching-signal query rather
  than spending the fast allowance as a target. Failure disables later
  observability without installation, repair, credential switching, or repeated
  checks.
