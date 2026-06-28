# Code Review Quality Rubric

Use this reference for strict implementation-quality reviews. Apply it with
judgment: focus on high-conviction structural issues and concrete simpler
shapes that preserve behavior.

## Core Standard

Perform a deep code quality audit of the current branch's changes. Rethink how
to structure or implement the changes to meaningfully improve code quality
without impacting behavior. Work to improve abstractions, modularity,
succinctness, and legibility. Be thorough, rigorous, and ambitious about
structural simplification.

## Primary Questions

- Is there a code-judo move that would make this dramatically simpler?
- Can the change be reframed so fewer concepts, branches, helper layers, or
  modes are needed?
- Does this improve or worsen the local architecture?
- Did the diff add branching complexity where a better abstraction should
  exist?
- Did a previously cohesive module become more coupled, more stateful, or
  harder to scan?
- Is the logic living in the right file, package, and layer?
- Did this change enlarge a file or component past a healthy size boundary?
- Are there repeated conditionals that signal a missing model, helper, policy,
  dispatcher, or state machine?
- Is the implementation direct and legible, or does it rely on special cases
  and incidental control flow?
- Is each abstraction earning its keep, or is it only a wrapper?
- Did the diff introduce casts, `any`, `unknown`, nullable modes, or ad-hoc
  object shapes that obscure the real invariant?
- Is feature logic leaking through a shared path or API boundary?
- Is orchestration more sequential or less atomic than it needs to be?

## Findings To Escalate

Treat these as high-priority review findings:

- A complicated implementation where a clearer reframing could delete whole
  categories of complexity.
- A refactor that moves code around without reducing the number of concepts a
  reader must hold in their head.
- A file crossing from below 1000 lines to above 1000 lines due to the change,
  especially when the new code could become a focused module or helper.
- New conditionals bolted onto unrelated code paths.
- One-off booleans, nullable modes, flags, or fallback branches that complicate
  existing control flow.
- Feature-specific logic leaking into general-purpose modules.
- Generic magic handling that hides simple data-shape assumptions.
- Thin wrappers or pass-through abstractions that add indirection without
  simplifying the caller.
- Unnecessary casts, optional parameters, `any`, `unknown`, or loosely shaped
  objects that muddy the contract.
- Copy-pasted logic where a helper or shared model would reduce complexity.
- Narrow edge-case handling inserted into an already busy function.
- Refactors that pass tests but make the code less modular or less readable.
- Temporary branches that are likely to become permanent debt.
- Bespoke helpers where the codebase already has a canonical utility.
- Logic added in the wrong layer, package, or service.
- Sequential async flow where independent work could run in parallel and make
  the orchestration simpler.
- Partial-update logic that makes state harder to reason about than an atomic
  structure would.

## Preferred Remedies

Prefer remedies that remove conceptual load:

- Delete a layer of indirection instead of polishing it.
- Reframe the state model so conditionals disappear.
- Move ownership so the feature becomes a natural extension of an existing
  abstraction.
- Turn special-case logic into a simpler default flow with fewer exceptions.
- Extract a helper or pure function when it removes duplication or isolates a
  stable concept.
- Split a large file into focused modules before it becomes hard to scan.
- Move feature-specific logic behind a dedicated abstraction.
- Replace condition chains with a typed model, explicit dispatcher, or policy
  object.
- Separate orchestration from business logic.
- Collapse duplicate branches into a single clearer flow.
- Delete wrappers that do not clarify the API.
- Reuse canonical helpers instead of near-duplicates.
- Make type boundaries explicit so control flow becomes simpler.
- Move logic to the package or layer that already owns the concept.
- Parallelize independent work when doing so also simplifies orchestration.
- Restructure related updates into a more atomic flow when partial state would
  be brittle.

## Approval Bar

Do not approve merely because behavior seems correct. The bar for approval is:

- no clear structural regression
- no obvious missed opportunity for dramatic simplification when a plausible
  path is visible
- no unjustified file-size explosion
- no spaghetti growth from special-case branching
- no hacky or magical abstraction that makes the code harder to reason about
- no wrapper, cast, or optionality churn obscuring the real design
- no architecture-boundary leak or avoidable canonical-helper duplication
- no missed decomposition that would materially improve maintainability

Treat these as presumptive blockers unless the author can justify them:

- The change preserves a lot of incidental complexity when a plausible
  simplification would delete it.
- The change pushes a file from below 1000 lines to above 1000 lines.
- The change tangles an existing flow with ad-hoc branching.
- The change solves a local feature problem by scattering feature checks across
  shared code.
- The change adds unnecessary wrappers, casts, or a looser contract that makes
  the design more indirect.
- The change duplicates an existing helper or puts logic in the wrong layer
  when a clear canonical home exists.

## Review Tone

Be direct, serious, and demanding about quality without being rude. Do not
soften major maintainability issues into mild suggestions. If the code makes
the codebase messier, say that clearly and propose a cleaner structure.

Useful phrasing:

- This pushes the file past 1k lines. Can we decompose this first?
- This adds another special-case branch into an already busy flow. Can we move
  this behind its own abstraction?
- This works, but it makes the surrounding code more tangled. Keep the behavior
  and restructure the implementation.
- This looks like feature logic leaking into a shared path. Can we isolate it?
- This abstraction seems unnecessary. Can we keep the direct flow?
- Why does this need a cast or optional here? Can we make the boundary explicit
  instead?
- This looks like a bespoke helper for something the codebase already owns.
  Can we reuse the canonical helper?
- There is a code-judo move here: reframe the model so these branches
  disappear.
- This refactor moves complexity around, but it does not delete it. Can the
  model become simpler?

## Output Priority

Prioritize findings in this order:

1. Structural code-quality regressions.
2. Missed opportunities for dramatic simplification.
3. Spaghetti or branching-complexity increases.
4. Boundary, abstraction, and type-contract problems.
5. File-size and decomposition concerns.
6. Modularity and abstraction issues.
7. Legibility and maintainability concerns.

Prefer a smaller number of high-conviction comments over a long list of
cosmetic notes.
