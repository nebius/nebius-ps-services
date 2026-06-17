# Agentic SDLC Failure Taxonomy

Classify the root cause before retrying. Route to the earliest SDLC phase that
can fix the cause.

| Class | Meaning | Route |
| --- | --- | --- |
| SPEC_GAP | Requirements are missing, ambiguous, contradictory, or not testable. | sdlc-create-requirements |
| CONTEXT_GAP | Vendor, internal, codebase, or test context is missing or unverifiable. | sdlc-gather-context |
| DESIGN_DEFECT | Design cannot satisfy requirements or lacks implementable boundaries. | sdlc-create-design |
| PLAN_DEFECT | Locked plan is stale, incomplete, unsafe, or inconsistent with design. | sdlc-create-plan |
| TEST_DEFECT | Test expectation, fixture, harness, or assertion is wrong. | sdlc-tdd |
| IMPLEMENTATION_DEFECT | Production code does not satisfy tests or acceptance behavior. | sdlc-implement-plan |
| VALIDATION_DEFECT | Syntax, lint, type, import, config, dependency, or build check fails. | sdlc-validate-codes or sdlc-implement-plan |
| EVALUATION_DEFECT | Observed behavior fails acceptance despite tests or validation. | sdlc-implement-plan or sdlc-create-design |
| UAT_DEFECT | Cross-feature or product-level acceptance fails. | classify to responsible phase |
| ENVIRONMENT_DEFECT | Tooling, dependency, auth, service, or local environment blocks proof. | human input or environment setup |
| POLICY_BLOCK | Safety policy, branch policy, hook, or explicit guardrail blocks action. | human input or safer workflow |
| HUMAN_INPUT_REQUIRED | A product, priority, credential, access, or approval decision is human-owned. | stop and ask |
| UNKNOWN_DEFECT | Evidence is insufficient to classify confidently. | gather minimum evidence |

Do not collapse all failures into implementation. Preserve the evidence path and
retry count for the responsible phase.
