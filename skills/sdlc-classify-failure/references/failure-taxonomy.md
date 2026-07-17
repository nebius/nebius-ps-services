# Agentic SDLC Failure Taxonomy

Classify the root cause before retrying. Route to the earliest SDLC phase that
can fix the cause.

| Class | Meaning | Route |
| --- | --- | --- |
| SPEC_GAP | Requirements are missing, ambiguous, contradictory, or not testable. | sdlc-create-requirements |
| CONTEXT_GAP | Vendor, internal, codebase, or test context is missing or unverifiable. | sdlc-gather-context |
| DESIGN_DEFECT | Design cannot satisfy requirements or lacks implementable boundaries. | sdlc-create-design |
| PLAN_DEFECT | Locked plan is stale, incomplete, unsafe, or inconsistent with design. | sdlc-create-plan |
| EXECUTION_PREPARATION_DEFECT | Plan parsing, dependency scheduling, or private integration preparation failed before workers started. | sdlc-prepare-execution or sdlc-create-plan |
| WORKTREE_CONFLICT | A registered project, integration, or worker checkout is dirty, moved, divergent, foreign, or has the wrong Git identity. | owning execution phase; preserve resources |
| REPLAN_REQUIRED | Write ownership, plan digest, source shape, or product truth changed after preparation. | sdlc-create-plan after preserving active execution evidence |
| TEST_DEFECT | Test expectation, fixture, harness, or assertion is wrong. | sdlc-tdd |
| IMPLEMENTATION_DEFECT | Production code does not satisfy tests or acceptance behavior. | sdlc-implement-plan |
| INTEGRATION_CONFLICT | Ordered worker integration cannot merge or its recorded ancestry/result identity is invalid. | sdlc-implement-plan; do not rewrite worker history |
| CLEANUP_BLOCKED | A worker or integration resource cannot be proven clean, reachable, and registered for non-force removal. | owning execution phase or human input |
| PROMOTION_BLOCKED | Project or integration identity/evidence drift prevents exact ff-only promotion. | sdlc-commit or responsible earlier phase |
| PROMOTION_FAILED | Fast-forward promotion ran but exact target equality could not be proven. | sdlc-commit and human input |
| WORKFLOW_UPGRADE_REQUIRED | Unfinished private execution uses unsupported schema v1. | stop and request a new v2 run decision |
| VALIDATION_DEFECT | Syntax, lint, type, import, config, dependency, or build check fails. | sdlc-validate-codes or sdlc-implement-plan |
| EVALUATION_DEFECT | Observed behavior fails acceptance despite tests or validation. | sdlc-implement-plan or sdlc-create-design |
| UAT_DEFECT | Cross-feature or product-level acceptance fails. | classify to responsible phase |
| ENVIRONMENT_DEFECT | Tooling, dependency, auth, service, or local environment blocks proof. | human input or environment setup |
| POLICY_BLOCK | Safety policy, branch policy, hook, or explicit guardrail blocks action. | human input or safer workflow |
| HUMAN_INPUT_REQUIRED | A product, priority, credential, access, or approval decision is human-owned. | stop and ask |
| UNKNOWN_DEFECT | Evidence is insufficient to classify confidently. | gather minimum evidence |

Do not collapse all failures into implementation. Preserve the evidence path and
retry count for the responsible phase.
