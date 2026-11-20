# Planning phases and delivery slices

Read this for a document-driven program before freezing its phase plan. The
SDLC root coordinates; context and design adapters supply evidence and design
decisions. This is not another planner or a public helper action.

## Identify the source plan

Inventory the complete source package before selecting the first delivery.
Resolve authority from its index, normative catalogs and explicit user intent.
Inspect phase tables, package dependencies, exit checks, qualification and
release gates together. Record conflicts rather than silently choosing a source.
Keep product implementation milestones distinct from SDLC skill phases,
installer stages, external prerequisites and runtime qualification campaigns.

When implementation phases are supplied:

- Preserve their identities, dependency constraints and exit obligations.
  Preserve package-to-milestone mappings when one package spans several phases.
- Split a large phase into multiple delivery slices without weakening its exit.
  A slice or scoped check does not close full acceptance or a release gate.
- Treat criterion IDs as identities, never an implied execution sequence.
  Trace explicit and semantic prerequisites, including security before exposure
  and containment before hostile execution.
- An unsafe, cyclic, contradictory or untestable source sequence needs an
  explicit design resolution and accepted revision. Do not repair it silently.

If a source label needs normalization to a valid program ID, record an explicit
one-to-one mapping in the planning rationale and retain the source label in the
design; for example, `8A` maps to `Phase-8A`. Preserve the mapped milestone's
dependencies and exit obligations. Freeze these normalized IDs with the plan.

## Derive phases when none are supplied

The root must perform this design step; do not require the user to invent phases
or silently flatten a large design into one feature. A small coherent objective
may need only one milestone. There is no universal phase count.

1. Map every accepted obligation to its observable outcome and canonical
   REQ/FEAT owners, including cross-cutting security, data and operability work.
2. Identify prerequisite edges from interfaces, data ownership, migrations,
   authorization, integration seams, operational constraints and test readiness.
   Distinguish implementation dependencies from optional campaign availability.
3. Select the smallest useful end-to-end outcome, with only the foundation work
   that actually blocks it. Name its exit criteria before ordering later work.
4. Group remaining outcomes into coherent milestones by dependency and risk.
   Bring bounded feasibility checks forward when their result changes later
   design. A spike produces evidence; it does not certify feature completion.
5. Keep candidate readiness and production qualification separate when the
   acceptance contract distinguishes them. External infrastructure prerequisites
   do not grant provisioning authority. Pending optional UAT keeps its existing
   policy; derivation must not turn it into an implementation gate.
6. Review the dependency graph and full obligation crosswalk. Record why these
   boundaries were chosen, alternatives, assumptions and unresolved conflicts.
   Future obligations remain scheduled even when their task detail is deferred.

Use `mode: supplied` when the package provides implementation milestones, even
if it needs finer slices. Use `mode: derived` only when those milestones are
absent; cite the requirements/design sources used for derivation. For mixed
inputs, preserve supplied milestones, explain derived refinements and their
mapping in the rationale, and settle the complete reviewed plan before admission.

## Freeze the reviewed result

The source manifest's `planning` record contains:

```json
{
  "mode": "derived",
  "sources": ["docs/contracts/requirements.md"],
  "rationale": "First prove the core outcome; then add the dependent outcome.",
  "phases": [
    {"id": "Core", "depends_on": [], "exit_criteria": ["AC-001"]},
    {"id": "Extension", "depends_on": ["Core"], "exit_criteria": ["AC-002"]}
  ]
}
```

Every referenced path must occur in the digest-checked source inventory.
The rationale is nonempty and bounded to 4096 characters; keep detailed design
with its existing owner. Exit criteria reference required source obligations.
The root reviews whether this normalized plan faithfully represents the source.
Mechanical checks validate references and structure, not semantic completeness.

Early inventory may use `planning: null` while requirements/context/design are
being reconciled. A program cannot be admitted or revalidated until planning is
complete. Filling or changing it changes the manifest digest: use the accepted
objective revision and impact/replanning path, never overwrite accepted bytes.
The program's phase definitions must exactly match this reviewed plan; both
initial admission and subsequent input verification enforce that equality.
The source manifest uses `maintain-project-specs/source-bundle-v2`; old manifests
are rejected without automatic conversion. Preserve historical run evidence.

## Bound each delivery

One phase may contain several slices, and one FEAT may span several slices.
Each slice needs one coherent observable result, source criteria and regression
coverage, prerequisite slices, REQ/FEAT mappings, required checks, rollback or
stop conditions and a reviewable change scope. Split again when unrelated
outcomes, unresolved contracts or validation/rollback breadth make review and
recovery unbounded. File counts and token counts alone are not completion oracles.

Elaborate detailed task graphs only for the next ready slices. Use vertical
flows through the relevant layers; isolate horizontal foundation work only
when it is a genuine prerequisite. Keep source dependencies in the task graph,
not lexical order. Context packs stay per FEAT; execution plans and receipts use
the selected FEAT or SLICE execution identity. One delivery candidate remains
active, with bounded parallel tasks and retained worktrees under existing owners.

Before dispatch, review source -> REQ -> FEAT -> phase -> slice -> task -> check
coverage. Before completion, use current exact-candidate evidence; progress,
merged slices and historical passing checks cannot certify the remaining program.

## Evidence and alternatives

Guidance alone is simpler but cannot reject divergence from an accepted plan.
A general prose-to-phase compiler would overstate semantic understanding.
Use root-reviewed planning plus deterministic admission at existing owners.
Revisit only if observed planning failures justify stronger source-specific
extraction; keep ambiguous interpretation explicit.

[DORA's small-batch guidance](https://dora.dev/capabilities/working-in-small-batches)
supports small testable increments and frequent integration, including for
AI-assisted development. [Anthropic's long-running-agent experiments](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
support a complete feature inventory, incremental progress and clean handoffs.
The phase derivation procedure above is this workflow's design synthesis, not a
vendor-prescribed sequence or a guarantee of model planning quality.

Deterministic supplied/derived fixtures prove admission, dependency and coverage
rules. Native planning quality, multi-slice delivery and actual protected remote
merge require separate observations; static fixtures never qualify those claims.
