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
| IMPLEMENTATION_DEFECT | Production code violates an accepted invariant inside an existing implementation boundary. | active task: sdlc-implement-plan; integrated failed or completed waves: corrective sdlc-create-plan |
| INTEGRATION_CONFLICT | Ordered worker integration cannot merge or its recorded ancestry/result identity is invalid. | sdlc-implement-plan; do not rewrite worker history |
| CLEANUP_BLOCKED | A worker or integration resource cannot be proven clean, reachable, and registered for non-force removal. | owning execution phase or human input |
| PROMOTION_BLOCKED | Project or integration identity/evidence drift prevents exact ff-only promotion. | sdlc-commit or responsible earlier phase |
| PROMOTION_FAILED | Fast-forward promotion ran but exact target equality could not be proven. | sdlc-commit and human input |
| WORKFLOW_UPGRADE_REQUIRED | Private execution uses a retired `agentic-sdlc/execution-coordinator-v1` through `v7` schema, including a completed record; or an unfinished run lacks its required accepted objective binding. | stop without mutation and start a new `workflow/execution-coordinator-v2` or objective-bound run |
| VALIDATION_DEFECT | A mechanical artifact, syntax, lint, type, import, config, dependency, or build check fails. | unprepared: exact supported authoring phase; prepared: sdlc-validate-codes |
| EVALUATION_DEFECT | The evaluator or evaluation harness is proven wrong, or the observed acceptance failure still lacks a proven owner. | proven evaluator defect: sdlc-evaluate; ambiguous cause: troubleshoot exactly once |
| UAT_DEFECT | Cross-feature or product-level acceptance fails. | classify to responsible phase |
| DOCUMENTATION_DRIFT | Project-facing documentation, examples, changelog, or requirement/design traceability no longer matches the accepted implementation and evidence. | sdlc-update-documents, then align before sealing; sealed/promoted/completed execution starts a corrective run through sdlc-create-plan |
| PR_HEAD_DRIFT | The local, remote, promoted, reviewed, or authorized PR head identities disagree after promotion. | stop without push/overwrite/merge and require human ownership input before sdlc-start |
| ENVIRONMENT_DEFECT | Tooling, dependency, auth, service, or local environment blocks proof. | active implementation: counted existing-task recovery; completed waves: unchanged gate recovery; otherwise stop for setup or human input |
| POLICY_BLOCK | Safety policy, branch policy, hook, or explicit guardrail blocks action. | human input or safer workflow |
| HUMAN_INPUT_REQUIRED | A product, priority, credential, access, or approval decision is human-owned. | stop and ask |
| UNKNOWN_DEFECT | Evidence is insufficient, missing, contradictory, or unresolved. | stop with exact missing evidence; never infer design |

Do not collapse all failures into implementation. Preserve the evidence path and
retry count for the responsible phase.

For lifecycle `unprepared`, a proven `VALIDATION_DEFECT` routes only to the
event's exact canonical phase: `sdlc-create-requirements`, `sdlc-gather-context`,
`sdlc-create-design`, `sdlc-auto-steering`, `sdlc-create-plan` or
`sdlc-prepare-execution`. The corrective mode is `phase_artifact_repair`;
unsupported or post-execution phase names stop. A counted localized dispatch
repairs the artifact and reruns its original check without manufacturing a
locked plan, integration checkout or basis. A candidate formatting failure is
not positive evidence of an architectural `DESIGN_DEFECT`; its stricter gate
and canonical publication ownership remain unchanged. Other lifecycles retain
their existing validation route, and uncertain causes still require diagnosis.

The canonical `diagnosis-v3` result `validation_defect` maps to
`VALIDATION_DEFECT`, including the required diagnosis after a failed direct
repair. Require proven or high-confidence causality, structured divergence and
causal proof, a bounded repair target, affected files and the original regression
check. The existing phase selection, counted dispatch and ceilings apply; a
diagnosis does not erase the first failure or grant architectural authority.
Retired active diagnosis records are rejected without conversion, including
diagnoses reached through older classifications. Validate reference identifiers
before lookup and bind diagnosis evidence to its event, execution and active
blocker before control operations. Rejected dispatch or completion requests do
not change counters or append transition intents.

Lifecycle `integrated_wave` preserves the interval between successful task
integration and failed combined validation. A proven localized diagnosis and
counted dispatch authorize execution-owned `wave-fail`; the corrective plan
preserves failed history, and successful correction records a separate
resolution. Never label this interval `active_task` or `waves_completed`.

A proven `ENVIRONMENT_DEFECT` during `active_task` at exactly
`sdlc-implement-plan` now routes through `existing_task_attempt`. Establish an
in-scope remedy and new evidence/hypothesis, then consume a counted localized
repair dispatch before authorizing the shared fresh-worker recovery. Preserve
the blocked receipt and observe termination and runtime release. Rerun the
original failed operation through an authorized mechanism, complete the
assignment's validation and normal task/wave completion, then record the actual
remediation result. Success requires the existing downstream revalidation
cursor; it does not erase failure history or grant permission. Other active
phases, policy/human blockers and later lifecycle boundaries gain no authority.

After completed waves, an environment block may resolve through private
`record-environment-recovery` only after its exact gate passes on the original
clean integrated commit with unchanged fingerprints. Preserve the immutable
failure and the failed trial outcome. This records observed recovery; it does
not prove a vendor root-cause fix or authorize recovery actions.
Keep the original phase in failure evidence and use its existing canonical
gate surface for invalidation and recovery. An unsupported phase remains
blocked without an executable recovery mode; earlier obligations remain pending.

`troubleshoot` is a conditional diagnostic route, not a happy-path phase. Test,
implementation, specification, evaluator, environment, policy, human, and
design causes that are already proven bypass it. Every troubleshooting result
returns through `sdlc-classify-failure`.

`DESIGN_DEFECT` is narrower than a difficult implementation. It requires a
proven change to architecture topology, component or service responsibility or
boundary, public interface, data ownership or lifecycle, migration behavior,
security boundary, or cross-component workflow. Probable or incomplete
causality cannot authorize redesign.

The alignment gate is owned by `align`. After a source repair changes gate
owner routes, the private `refresh-revalidation-routes` action requires the
exact pending cursor and an evidence reference. It verifies the original repair
authority and unchanged invalidated surfaces, archives the old cursor and
progress, and resets all gates to pending under the current policy. Immutable
evidence and repair budgets remain unchanged. A retry preserves any new gate
progress; this is not a general reset or an alias for retired skill names.

## Successive Failure Obligations

A new failure or diagnosis archives the previous revalidation cursor and its
authorizing context before replacing the active projection. Routed
classifications carry immutable references to prior invalidations and the
canonical ordered union of earlier and newly required gates. A successful
counted repair restarts that entire sequence under its own dispatch, with no
reused gate receipts or commit binding. Earlier receipts remain historical;
exact replay preserves current progress and counters. The new blocker keeps
its own failure type, route and budget. Environment-only recovery still rejects
unrelated pending invalidations. The last allowed dispatch may finish its
revalidation; another dispatch and over-limit state remain blocked.
