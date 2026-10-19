# Workflow Handoffs

## Agentic SDLC Diagnostic Mode

When `sdlc-classify-failure` routes an active Agentic SDLC failure here:

- Operate as a conditional diagnostic branch, not a mandatory workflow phase.
- Preserve the failed evaluation, exact integration commit, environment
  identity, accepted criteria, fingerprints, stable blocker key, and repair
  budget.
- Use the registered private integration worktree only. Temporary diagnostic
  instrumentation must be explicitly scoped, reversible, uncommitted, removed
  before handoff, and absent from the final diff.
- Do not commit product fixes, change tests or specifications, weaken
  acceptance, invoke general `design`, or call an SDLC design, plan, or
  implementation phase directly.
- Return one `diagnosis-v1` to `sdlc-classify-failure`. The classifier remains
  authoritative for diagnosis validation, taxonomy, budget accounting,
  invalidation, and the next owner.
- The result must be exactly one of: localized implementation defect, test
  defect, evaluator defect, environment defect, specification gap, proven
  system-contract defect, missing decisive evidence, unresolved competing
  hypotheses, policy block, or human input.
- "No implementation bug found" is missing or unresolved evidence. It never
  becomes a design defect without positive causal proof.

A localized implementation handoff must record expected and observed behavior,
the stable blocker and exact regression oracle, earliest divergent component,
operation, and source boundary, violated invariant and causal chain, affected
files and bounded repair target, counterfactual, alternatives eliminated,
confidence, required regression test, evidence references, and constraints to
preserve. It defines the bug and repair boundary, not a speculative patch.

- Classify the proposed remedy before editing as either a localized
  invariant restoration or a design-scale change.
- A design-scale remedy changes at least one system contract: architecture
  topology; component or service responsibilities or boundaries; a public
  interface; data ownership or lifecycle; a migration; or a cross-component
  workflow. Implementation size, algorithmic complexity, concurrency
  difficulty, or a large rewrite inside one existing private boundary does
  not make a repair design-scale.
- Outside an active Agentic SDLC workflow, use `design` after causal proof
  and before implementation only for a design-scale remedy. Give `design`
  the proven causal chain, violated invariant, requirements, constraints,
  non-goals, fixed technologies, and regression oracle. `design` owns
  solution design and the `/plan` handoff; it must not reopen diagnosis or
  implement the change. Return the completed handoff to the appropriate
  implementation workflow, while `troubleshoot` retains verification and
  final causal reporting.
- Inside an active Agentic SDLC workflow, send the proven causal handoff to
  `sdlc-classify-failure` instead of calling general `design` or a design
  phase directly. The classifier must reload current state, record the
  failure class and retry accounting, and set `next_recommended_skill`;
  the SDLC coordinator then routes to the recorded design, plan, or other
  owning phase.
- In Agentic SDLC diagnostic mode, stop at the causal handoff. Emit
  `diagnosis-v1`, remove temporary instrumentation, and return to
  classification without entering `REMEDIATED`.
