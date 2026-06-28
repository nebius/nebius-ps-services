# Software System Design Principles

Use this reference as a design-phase checklist before implementation. It is
vendor-neutral and project-agnostic. Apply the relevant sections based on the
decision's risk, blast radius, reversibility, and uncertainty; do not turn the
checklist into ceremony for small changes.

## Decision Worksheet

Before comparing options, write a compact design brief:

- Decision: the choice being made.
- Outcome: the business or user behavior the design should improve.
- Scope: users, systems, data, APIs, teams, and workflows affected.
- Constraints: budget, deadline, team size, existing platforms, compliance,
  data residency, scale, latency, availability, and operational maturity.
- Options: at least one simple baseline plus any stronger alternatives.
- Trade-offs: what each option improves, worsens, costs, and risks.
- Revisit trigger: the condition that should reopen the decision.
- Validation: tests, prototypes, measurements, threat models, migration checks,
  runbooks, or vendor-doc checks needed before or after implementation.

## Business And Product Intent

- **1. Business outcome first:** Start with the business problem, user outcome,
   and measurable success signal. A design without an outcome drifts into tool
   preference.
- **2. Use-case clarity:** Define primary journeys, system actions, edge cases,
   and failure cases before choosing services, databases, queues, or
   frameworks.
- **3. Explicit quality attributes:** Treat reliability, latency, throughput,
   security, cost, privacy, compliance, and operability as design inputs, not
   late-stage expectations.
- **4. Constraint awareness:** Name hard constraints early: budget, team size,
   deadline, regulation, data residency, existing platforms, and operational
   maturity.
- **5. Design for change:** Assume requirements, scale, ownership, and
   integrations will change. Prefer designs that evolve in small, reversible
   steps.
- **6. Decision traceability:** Record major decisions with context,
   alternatives, trade-offs, and consequences so future maintainers understand
   why the design exists.
- **7. Trade-off discipline:** Avoid universal good/bad labels. State what each
   choice improves, what it worsens, and when it should be revisited.
- **8. Simplicity bias:** Choose the simplest design that satisfies current and
   near-future requirements. Complexity taxes onboarding, testing, debugging,
   security, and delivery speed.
- **9. Build, buy, reuse, or delegate:** Decide explicitly whether a capability
   belongs in custom code, a platform, a vendor service, or a shared internal
   service.
- **10. Success and failure definition:** Define normal, degraded, and
    unacceptable operation so fallback, alerting, recovery, and user
    communication have a target.

## Domain And Model Design

- **11. Domain-first modeling:** Understand the business domain before drawing
    technical components. Boundaries should follow concepts, rules, workflows,
    and ownership.
- **12. Shared language:** Use the same terms in requirements, diagrams, APIs,
    database names, tests, and code. Divergent language creates translation
    bugs.
- **13. Bounded contexts:** Split the domain where words, rules, or
    responsibilities change meaning. A term such as customer, account, or order
    may not mean one thing everywhere.
- **14. Core-domain focus:** Spend the most design effort where the business has
    strategic or competitive advantage.
- **15. Supporting-domain pragmatism:** Keep necessary but non-strategic domains
    straightforward. Do not over-engineer commodity workflows.
- **16. Generic-domain reuse:** Prefer proven platforms or shared services for
    common capabilities such as authentication, billing, logging, scheduling,
    and notifications unless differentiation justifies custom work.
- **17. Aggregate boundaries:** Put transactional consistency around business
    invariants that must always be true together, not around unrelated data that
    happens to be convenient to store together.
- **18. Domain events:** Model important business facts as past-tense events,
    such as `InvoiceApproved` or `UserRegistered`. Events describe what
    happened, not what someone hopes will happen.
- **19. Anti-corruption layers:** Translate legacy systems, vendor APIs, and
    external schemas at the boundary so foreign concepts do not leak into the
    core model.
- **20. Context maps:** Draw how bounded contexts depend on each other, who owns
    each model, and how data moves between them. Unknown relationships become
    hidden coupling.

## Modularity And Boundaries

- **21. High cohesion:** Put things together when they change for the same
    reason. A module, service, or package should have a clear responsibility.
- **22. Low coupling:** Keep components independent unless a real requirement
    connects them. Every dependency adds coordination, testing, and change
    risk.
- **23. Deep modules:** Prefer simple interfaces backed by meaningful internal
    capability. Shallow wrappers that expose internals rarely reduce
    complexity.
- **24. Information hiding:** Hide decisions likely to change, such as storage
    format, vendor API, caching strategy, algorithm choice, or framework
    details.
- **25. Stable dependency direction:** Make volatile details depend on stable
    policies, not the reverse. Business rules should not depend directly on UI,
    database, broker, or cloud SDK details.
- **26. Boundary purpose:** Give every boundary a reason: ownership, scalability,
    security, deployment independence, domain separation, or data ownership.
- **27. Avoid accidental layers:** Add layers only when they separate real
    responsibilities. Template-driven layers that split one feature across many
    files add friction.
- **28. Explicit workflows:** Represent important workflows directly. Workflows
    hidden across controllers, services, callbacks, and triggers are hard to
    reason about.
- **29. Pure policy:** Keep core business policy free from transport,
    persistence, framework, and infrastructure assumptions.
- **30. Minimal temporal coupling:** Avoid APIs where callers must know fragile
    call sequences. Make valid usage obvious and invalid usage hard.

## Data Design

- **31. Access-pattern-driven storage:** Choose storage from read patterns, write
    patterns, consistency needs, growth expectations, and operational maturity,
    not popularity.
- **32. Data ownership:** Assign one clear owner for each data entity. Shared
    writable databases across services create hidden coupling.
- **33. Source of truth:** Define which component owns each authoritative fact.
    Copies, indexes, caches, projections, and reports are derived data.
- **34. Rebuildable derived data:** Design caches, search indexes, read models,
    and materialized views so they can be rebuilt from authoritative sources.
- **35. Consistency by need:** Use strong consistency only where the business
    requires it. Eventual consistency with reconciliation is often safer and
    cheaper.
- **36. Transaction boundary discipline:** Keep transactions local to a clear
    consistency boundary. Avoid distributed transactions unless atomic behavior
    across boundaries is a true business requirement.
- **37. Idempotency by design:** Make commands, messages, and external calls safe
    to retry. Duplicate delivery is normal in distributed systems.
- **38. Data lifecycle:** Define how data is created, read, updated, archived,
    deleted, retained, and audited, especially for privacy and compliance.
- **39. Schema evolution:** Plan additive changes, compatibility windows,
    migrations, rollbacks, and old/new runtime coexistence before changing
    schemas.
- **40. Analytical separation:** Separate transactional models from reporting,
    analytics, and machine-learning models. One model rarely serves all access
    patterns well.

## API And Integration Design

- **41. Contract-first APIs:** Design contracts before implementation. Include
    business capability, input rules, output guarantees, errors, versioning,
    and ownership.
- **42. Consumer empathy:** Shape APIs around the consumer workflow, not provider
    internals. If consumers need provider internals, the boundary is weak.
- **43. Compatibility planning:** Treat breaking changes as costly. Prefer
    additive fields, tolerant readers, explicit versions, and planned migration
    windows when compatibility is required.
- **44. Explicit error model:** Make errors part of the contract. Callers need to
    distinguish retryable, user-correctable, permission, dependency, and
    permanent failures.
- **45. Coarse-grained use-case APIs:** Prefer business actions over chatty
    low-level operations. Fine-grained remote calls increase latency, coupling,
    and failure points.
- **46. Async where natural:** Use asynchronous communication for long-running
    work, cross-team workflows, external dependencies, and operations that can
    complete later.
- **47. Sync where necessary:** Use synchronous calls when the caller truly needs
    an immediate answer to continue. Avoid async machinery for simple local
    workflows.
- **48. Event meaning:** Publish events that represent stable facts, not internal
    implementation steps. Consumers should react to business meaning.
- **49. Integration isolation:** Put third-party integrations behind internal
    interfaces because vendor APIs change, fail, rate-limit, and use different
    models.
- **50. Version ownership:** Assign owners for API versions, deprecation
    timelines, migration guides, and consumer communication.

## Reliability And Failure Design

- **51. Failure as normal:** Design for dependency failures, network partitions,
    full disks, backed-up queues, expired credentials, and user retries.
- **52. Reliability targets:** Define realistic reliability targets per user
    journey. Not every feature needs the same availability.
- **53. Graceful degradation:** Decide which capabilities can be reduced,
    delayed, cached, hidden, or made read-only during partial failure.
- **54. Timeout strategy:** Set timeouts for every remote call. Waiting forever
    consumes resources and spreads dependency failures.
- **55. Retry strategy:** Use bounded retries with backoff, jitter, and
    idempotency. Blind retries amplify outages.
- **56. Circuit breaking:** Stop calling unhealthy dependencies when failure is
    likely. Fast failure protects the rest of the system.
- **57. Bulkheads:** Isolate critical workloads from noisy or failing workloads
    with separate pools, queues, limits, or partitions.
- **58. Purposeful redundancy:** Add replicas, regions, queues, or databases only
    when they improve a defined reliability goal.
- **59. Recovery design:** Define backup, restore, replay, reconciliation, and
    disaster recovery before launch. Restore tests validate backup assumptions.
- **60. Human recovery path:** Provide safe operational actions during incidents:
    runbooks, admin tools, audit logs, and clear ownership.

## Performance, Scale, And Cost

- **61. Performance budgets:** Define latency, throughput, memory, storage, and
    concurrency targets per critical path before implementation.
- **62. Scale dimensions:** Identify what grows: users, requests, data, tenants,
    regions, integrations, or background jobs. Scale the dimension that matters.
- **63. Horizontal scalability:** Prefer designs that scale by adding units when
    workload requires it. Avoid single components that must grow without bound.
- **64. Load shedding:** Decide how the system rejects, queues, delays, or
    downgrades work under overload. Controlled rejection beats collapse.
- **65. Backpressure:** Make producers and consumers respect limits so fast
    components cannot overwhelm slow components.
- **66. Intentional caching:** Cache only with clear rules for freshness,
    invalidation, ownership, and failure behavior.
- **67. Cost visibility:** Estimate cost drivers during design: storage growth,
    network transfer, compute spikes, managed service pricing, observability
    volume, and retention.
- **68. Cost-performance trade-offs:** Decide where speed is worth spend and
    where slower behavior is acceptable.
- **69. Capacity margin:** Leave headroom for spikes, retries, deployments, and
    dependency slowdown. Operating at the limit makes variance dangerous.
- **70. Sustainability awareness:** Avoid wasteful overprovisioning, unnecessary
    retention, excessive polling, and duplicate processing.

## Security, Privacy, And Governance

- **71. Threat modeling:** Identify assets, likely attackers, trust boundaries,
    abuse paths, and failure impact during design.
- **72. Least privilege:** Give users, services, roles, tokens, and automation
    only the permissions needed.
- **73. Secure defaults:** Make the safe path the default for encryption,
    authentication, authorization, validation, logging, and secret handling.
- **74. Trust boundaries:** Draw where data crosses users, services, networks,
    tenants, vendors, and administrative systems. Protect every boundary.
- **75. Data classification:** Classify data sensitivity before designing
    storage, logging, access, retention, sharing, analytics, or test data.
- **76. Privacy by design:** Collect the minimum data needed, retain it only as
    long as required, and define deletion behavior.
- **77. Auditability:** Design audit trails for important business, security, and
    administrative actions: who did what, when, from where, and with what
    result.
- **78. Secret management:** Define how secrets are created, stored, rotated,
    accessed, revoked, and audited. Hardcoded secrets and manual sharing are
    design failures.
- **79. Abuse resistance:** Design for misuse such as spam, scraping, privilege
    escalation, replay, fraud, and resource exhaustion.
- **80. Governance fit:** Use lightweight governance that improves decisions
    without blocking flow. Heavy approval systems create bypasses.

## Operability And Observability

- **81. Observability by design:** Define logs, metrics, traces, events,
    dashboards, and alerts before implementation.
- **82. User-journey monitoring:** Monitor critical user journeys, not only
    infrastructure health. A healthy server can still fail checkout, login,
    search, or provisioning.
- **83. Correlation IDs:** Carry request, job, and event correlation across
    components so distributed debugging is possible.
- **84. Meaningful health checks:** Health checks should reflect readiness,
    dependency state, and user-impacting capability, not only process liveness.
- **85. Operational ownership:** Name who owns the system in production, who
    responds to alerts, and who approves architecture changes.
- **86. Safe deployment design:** Plan rolling deploys, canaries, feature flags,
    rollback, and old/new version compatibility.
- **87. Configuration clarity:** Separate code, environment configuration,
    secrets, and runtime policy. Validate configuration and make safe changes
    visible.
- **88. Admin capability:** Provide support, moderation, repair, replay, and
    investigation tools so engineers do not edit production data manually.
- **89. Toil reduction:** Automate or make self-service the repetitive
    operational tasks that would otherwise consume engineering time.
- **90. Incident learning loop:** Make incidents produce useful evidence through
    logs, metrics, traces, audit events, and decision records.

## Team, Delivery, And Evolution

- **91. Team-aligned boundaries:** Align software boundaries with team ownership
    where possible. Architecture that requires many teams for every change
    slows delivery.
- **92. Cognitive load limits:** Keep components understandable, operable, and
    changeable by the owning team.
- **93. Platform as product:** When designing platforms, treat developers as
    users. Provide stable interfaces, documentation, examples, support, and
    adoption paths.
- **94. Golden paths:** Provide recommended design and delivery paths for common
    cases. Standardization should reduce decision fatigue without blocking
    justified exceptions.
- **95. Evolutionary fitness checks:** Define automated checks for important
    architecture rules such as dependency direction, API compatibility,
    security policy, and infrastructure standards.
- **96. Reversible decisions:** Prefer changeable choices when uncertainty is
    high. Spend more design effort on irreversible choices.
- **97. Incremental migration:** Design major changes as expand-contract
    migrations, parallel runs, adapters, or strangler patterns. Avoid big-bang
    rewrites when safer paths exist.
- **98. Technical debt visibility:** Record intentional shortcuts with reason,
    risk, owner, and revisit trigger.
- **99. Design review quality:** Review assumptions, trade-offs, failure modes,
    security, data ownership, operability, and team ownership, not only the
    diagram.
- **100. Conway awareness:** Shape communication paths and software boundaries
     together because system architecture tends to mirror team communication.
