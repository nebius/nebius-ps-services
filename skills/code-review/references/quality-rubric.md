# Code Review Quality Rubric

Use this reference for neutral, evidence-based code reviews. Apply it with
judgment: focus on high-conviction findings, realistic failure modes, and
concrete simpler shapes that preserve behavior.

## Core Standard

Review the current local branch, local diff, changed files, module, repository
area, or provided patch for bug risk and implementation quality. Infer language,
tooling, build commands, test commands, and architecture from repository
evidence instead of assuming a stack-specific norm.

The review should be direct but neutral. Critique the code and the risk, not
the author. Do not flag personal style preferences unless the repository has a
documented standard.

## Review Modes

Choose the narrowest mode that satisfies the user's request.

- Diff review: for a pull request diff, patch, branch comparison, staged
  changes, or uncommitted changes. Focus on changed behavior, regressions,
  tests, compatibility, reliability, security-adjacent risk, and maintainable
  implementation.
- Module review: for a package, service, command, component, or feature area.
  Focus on boundaries, data flow, public interfaces, ownership, dependency
  usage, tests, and operational failure paths.
- Baseline review: for a full repository or subsystem health review. Focus on
  architecture, dependency posture, test strategy, operational readiness,
  maintainability, and systemic risk.
- Release-readiness review: when the user asks whether code is safe to ship.
  Focus on CI, rollback, observability, compatibility, migrations, feature
  flags, and operator procedures. Use `review-pr` for GitHub PR readiness.

If the user names a GitHub PR by number, URL, or current branch, route to
`review-pr` even when the request asks for bugs, regressions, tests, or code
quality.

## Evidence Standard

- Base findings on observed code, repository documentation, tests, CI
  configuration, logs, or reproducible reasoning.
- When something cannot be verified, say so and state the assumption or
  confidence level.
- Do not claim the code is secure, correct, reliable, production-ready, or
  bug-free beyond the evidence reviewed.
- Do not assume dependency, framework, runtime, or language behavior when that
  assumption is material. Check repository evidence or authoritative
  documentation.
- Treat generated files, lockfiles, build scripts, CI workflows, release
  scripts, dependency manifests, and permission changes as reviewable when
  they affect runtime behavior.

## Severity Rubric

- P0 Critical: exploitable security issue, data loss, auth bypass, secret
  exposure, irreversible corruption, production outage risk, or a change that
  should not merge or ship.
- P1 High: likely bug, regression, race condition, unsafe migration, broken
  contract, missing critical test, major reliability issue, or security
  weakness that should be fixed before merge.
- P2 Medium: maintainability, test, error-handling, observability,
  performance, compatibility, or edge-case issue that materially increases
  future risk but may not block every merge.
- P3 Low: minor cleanup, naming, documentation, small refactor, non-blocking
  test improvement, or style issue backed by repository standards.
- Nit: optional polish that should never block unless the repository explicitly
  treats it as required.

## Blocking Conditions

Request changes when any of these are present:

- Correctness: code likely fails the stated requirement, breaks an existing
  contract, mishandles a realistic edge case, or introduces inconsistent state.
- Security: code weakens authentication, authorization, input handling,
  secrets management, sensitive-data handling, tenant isolation, dependency
  safety, or secure defaults.
- Data safety: code can lose, corrupt, duplicate, expose, or irreversibly
  transform data without safeguards.
- Reliability: critical paths lack safe handling for retries, timeouts,
  partial failure, concurrency, cancellation, idempotency, or resource cleanup.
- Testing: behavior changes have no meaningful tests and cannot be justified
  as documentation-only, configuration-only, or truly mechanical refactoring.
- Operations: code can break deployment, rollback, migration order,
  observability, runtime configuration, or on-call diagnosis without a
  mitigation plan.
- Reviewability: the change is too large, mixed-purpose, generated without
  clear provenance, or impossible to understand safely from the available
  context.

## Review Map

Before line review, map enough context to avoid shallow findings:

- Scope: changed files, public interfaces, callers, downstream consumers,
  configuration, tests, docs, and ownership boundaries.
- Intent: prompt, issue, PR description, commit messages, tests, design notes,
  or nearby documentation that explain the goal.
- Risk areas: security boundaries, data loss risk, concurrency, migrations,
  external integrations, user-facing behavior, irreversible operations,
  performance hot paths, and deployment or rollback paths.
- Validation: tests, linters, type checks, builds, CI status, or local checks
  that are relevant and safe to inspect or run.

## Correctness And Edge Cases

Check whether:

- expected behavior works for normal inputs and states
- empty, null, missing, malformed, duplicate, large, slow, stale, expired,
  unauthorized, and out-of-order cases are handled where relevant
- invalid state transitions are prevented and valid transitions are explicit
- errors are handled intentionally and do not leave partial state
- repeated requests, retries, or reruns do not create unintended duplicates or
  corruption
- callers and downstream consumers still receive valid behavior

## Tests And Verification

Check whether:

- tests cover the user-visible or contract-visible behavior changed by the code
- at least one test would fail for the bug or failure mode being fixed
- important boundary, negative, and error cases are covered
- integration boundaries with storage, external systems, serialization,
  configuration, or public interfaces are verified where relevant
- tests are deterministic, readable, isolated enough for their purpose, and not
  overly coupled to implementation details
- local verification matches repository CI expectations where possible

Treat coverage as a risk signal, not proof of correctness.

## Security And Privacy

Within an implementation review, identify security-adjacent risks in changed
code. For a dedicated security scan, threat model, or remediation workflow, use
`apply-security`.

Check whether:

- trust boundaries and untrusted inputs are identified
- validation and canonicalization happen at the correct boundary
- identity assumptions are explicit and enforced
- authorization checks cover resource, tenant, action, and caller context
- secrets are not logged, committed, exposed, passed through unsafe channels,
  or stored insecurely
- sensitive data exposure is minimized in collection, retention, access, logs,
  metrics, and errors
- user-controlled data is not interpreted as commands, queries, paths,
  templates, or code without safe binding or escaping
- new or changed dependencies are necessary, maintained, and not obviously
  unsafe from available repository evidence
- security-relevant actions leave useful, non-sensitive audit evidence

## Reliability And Operations

Check whether:

- external calls and long-running operations have bounded execution
- retries are bounded, use backoff where appropriate, and are safe for the
  operation
- the system can recover or degrade safely when one dependency fails
- shared state, ordering, locks, async execution, and race conditions are
  handled deliberately
- files, sockets, memory, handles, temporary resources, and background work are
  cleaned up
- load, queue growth, rate limits, and resource exhaustion are bounded
- logs, metrics, traces, audit events, health signals, and error messages make
  new failure modes diagnosable without exposing sensitive data
- rollback, reconciliation, repair, or runbook changes are clear where needed

## Design And Maintainability

Perform a deep code-quality audit of the changed implementation. Rethink how
to structure or implement the changes to meaningfully improve code quality
without changing behavior. Work to improve abstractions, modularity,
succinctness, and legibility.

Primary questions:

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
- Are repeated conditionals signaling a missing model, helper, policy,
  dispatcher, or state machine?
- Is the implementation direct and legible, or does it rely on special cases
  and incidental control flow?
- Is each abstraction earning its keep, or is it only a wrapper?
- Did the diff introduce casts, `any`, `unknown`, nullable modes, optional
  flags, or ad-hoc object shapes that obscure the real invariant?
- Is feature logic leaking through a shared path or API boundary?
- Is orchestration more sequential or less atomic than it needs to be?

## Findings To Escalate

Treat these as high-priority review findings:

- A likely bug, broken contract, or realistic regression path.
- A missing critical test for behavior that changed.
- Security, data, reliability, or operations risk that can fail in production.
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
- Refactors that pass tests but make the code less modular or less readable.
- Temporary branches that are likely to become permanent debt.
- Bespoke helpers where the codebase already has a canonical utility.
- Logic added in the wrong layer, package, or service.
- Sequential async flow where independent work could run in parallel and make
  orchestration simpler.
- Partial-update logic that makes state harder to reason about than an atomic
  structure would.

## Preferred Remedies

Prefer remedies that reduce risk and remove conceptual load:

- Fix the smallest behavior path that proves the bug, then add or adjust the
  test that would have caught it.
- Add a boundary, negative, or failure-path test when the implementation risk
  is real and the current suite would not fail.
- Move ownership so the feature becomes a natural extension of an existing
  abstraction.
- Reframe the state model so conditionals disappear.
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

- no likely bug, regression, broken contract, unsafe data path, or critical
  missing test
- no visible security-adjacent or reliability issue that needs owner review or
  remediation before merge
- no clear structural regression
- no obvious missed opportunity for dramatic simplification when a plausible
  path is visible
- no unjustified file-size explosion
- no spaghetti growth from special-case branching
- no hacky or magical abstraction that makes the code harder to reason about
- no wrapper, cast, or optionality churn obscuring the real design
- no architecture-boundary leak or avoidable canonical-helper duplication
- no missed decomposition that would materially improve maintainability

Use these review decisions:

- Approve: no blocking finding found in the reviewed scope.
- Request changes: one or more P0/P1 blockers, critical missing tests, or
  structural blockers should be fixed before merge or release.
- Needs owner review: the risk crosses a security, data, operations, product,
  or domain boundary that cannot be judged from code evidence alone.
- Blocked by missing context: the review cannot be completed safely without a
  required diff, base, docs, tests, CI signal, or owner input.

## Review Tone

Be specific, neutral, actionable, proportional, transparent, and concise.
Reserve blocking language for real merge or release risks.

Useful phrasing:

- This can fail when `<scenario>` because `<evidence>`. Add `<minimal fix>` and
  prove it with `<test>`.
- This change has no test that would fail for `<failure mode>`. That makes the
  regression risk hard to accept.
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

1. Exploitable security issues, data loss, production outage risk, or
   irreversible corruption.
2. Likely bugs, regressions, broken contracts, and unsafe state transitions.
3. Critical missing tests for changed behavior or realistic failure modes.
4. Reliability, concurrency, operations, deployment, rollback, and
   observability risks.
5. Structural code-quality regressions and missed dramatic simplifications.
6. Spaghetti or branching-complexity increases.
7. Boundary, abstraction, ownership, and type-contract problems.
8. File-size and decomposition concerns.
9. Legibility and maintainability concerns.

Prefer a smaller number of high-conviction comments over a long list of
cosmetic notes.
