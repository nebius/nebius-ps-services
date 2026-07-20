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

## Boundary Cases

- A deterministic test failure with an obvious incorrect expected value should
  stay with ordinary implementation.
- Repeated failed attempts, multiple symptoms, or an unclear cross-layer cause
  should activate troubleshooting even when the first symptom is a test failure.
- A known Terraform, Helm, or cloud implementation should use the domain skill;
  an unexplained failure in the deployed stack should use troubleshooting and
  consult the domain skill for product-specific commands.
- A dedicated security scan should use `apply-security`; an authentication or
  authorization failure with an unknown causal mechanism may use troubleshooting
  while applying security guardrails.
