# Agentic SDLC Failure Taxonomy

Classify the root cause before retrying. Route to the earliest SDLC phase that
can fix the cause.

| Class | Meaning | Route |
| --- | --- | --- |
| SPEC_GAP | Requirements are missing, ambiguous, contradictory, or not testable. | sdlc-create-requirements |
| CONTEXT_GAP | Vendor, internal, codebase, or test context is missing or unverifiable. | sdlc-gather-context |
| DESIGN_DEFECT | Positive causal evidence proves the accepted requirement cannot be satisfied without changing a system contract. | design admission gate, then sdlc-create-design |
| PLAN_DEFECT | Locked plan is stale, incomplete, unsafe, or inconsistent with design. | sdlc-create-plan |
| EXECUTION_PREPARATION_DEFECT | Plan parsing, dependency scheduling, or private integration preparation failed before workers started. | sdlc-prepare-execution or sdlc-create-plan |
| WORKTREE_CONFLICT | A registered project, integration, or worker checkout is dirty, moved, divergent, foreign, or has the wrong Git identity. | owning execution phase; preserve resources |
| REPLAN_REQUIRED | Write ownership, plan digest, source shape, or product truth changed after preparation. | sdlc-create-plan after preserving active execution evidence |
| TEST_DEFECT | Test expectation, fixture, harness, or assertion is wrong. | sdlc-tdd |
| IMPLEMENTATION_DEFECT | Production code violates an accepted invariant inside an existing implementation boundary. | active task: sdlc-implement-plan; completed waves: corrective sdlc-create-plan |
| INTEGRATION_CONFLICT | Ordered worker integration cannot merge or its recorded ancestry/result identity is invalid. | sdlc-implement-plan; do not rewrite worker history |
| CLEANUP_BLOCKED | A worker or integration resource cannot be proven clean, reachable, and registered for non-force removal. | owning execution phase or human input |
| PROMOTION_BLOCKED | Project or integration identity/evidence drift prevents exact ff-only promotion. | sdlc-commit or responsible earlier phase |
| PROMOTION_FAILED | Fast-forward promotion ran but exact target equality could not be proven. | sdlc-commit and human input |
| WORKFLOW_UPGRADE_REQUIRED | Private execution uses unsupported coordinator schema v1 through v6, including a completed record; or an unfinished run lacks its required managed prompt binding. | stop without mutation and start a new coordinator-v7 or prompt-bound run |
| VALIDATION_DEFECT | Syntax, lint, type, import, config, dependency, or build check fails. | sdlc-validate-codes or sdlc-implement-plan |
| EVALUATION_DEFECT | The evaluator or evaluation harness is proven wrong, or the observed acceptance failure still lacks a proven owner. | proven evaluator defect: sdlc-evaluate; ambiguous cause: troubleshoot exactly once |
| UAT_DEFECT | Cross-feature or product-level acceptance fails. | classify to responsible phase |
| DOCUMENTATION_DRIFT | Project-facing documentation, examples, changelog, or requirement/design traceability no longer matches the accepted implementation and evidence. | sdlc-update-documents, then align |
| PR_HEAD_DRIFT | The local, remote, promoted, reviewed, or authorized PR head identities disagree after promotion. | stop without push/overwrite/merge and require human ownership input before sdlc-start |
| ENVIRONMENT_DEFECT | Tooling, dependency, auth, service, or local environment blocks proof. | human input or environment setup |
| POLICY_BLOCK | Safety policy, branch policy, hook, or explicit guardrail blocks action. | human input or safer workflow |
| HUMAN_INPUT_REQUIRED | A product, priority, credential, access, or approval decision is human-owned. | stop and ask |
| UNKNOWN_DEFECT | Evidence is insufficient, missing, contradictory, or unresolved. | stop with exact missing evidence; never infer design |

Do not collapse all failures into implementation. Preserve the evidence path and
retry count for the responsible phase.

`troubleshoot` is a conditional diagnostic route, not a happy-path phase. Test,
implementation, specification, evaluator, environment, policy, human, and
design causes that are already proven bypass it. Every troubleshooting result
returns through `sdlc-classify-failure`.

`DESIGN_DEFECT` is narrower than a difficult implementation. It requires a
proven change to architecture topology, component or service responsibility or
boundary, public interface, data ownership or lifecycle, migration behavior,
security boundary, or cross-component workflow. Probable or incomplete
causality cannot authorize redesign.
