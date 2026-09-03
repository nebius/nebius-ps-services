# Application Stack Selection Framework

Use this framework after reading the user request and current project evidence.
Choose architecture from requirements before evaluating named products.

## Contents

- [Decision Brief](#decision-brief)
- [Application Archetypes](#application-archetypes)
- [Selection Axes](#selection-axes)
- [Architecture Decisions](#architecture-decisions)
- [Component Admission Test](#component-admission-test)
- [Decision Record](#decision-record)

## Decision Brief

Capture the smallest brief that can change the recommendation:

- Product outcome and primary user journey.
- Greenfield or brownfield, including locked technologies and migration cost.
- Client platforms: browser, mobile, desktop, terminal, machine API, device, or
  mixed.
- Rendering, navigation, offline, accessibility, and public-discovery needs.
- Core domain, transactional invariants, authoritative data, and retention.
- Integrations, asynchronous work, scheduled work, durable workflows, and
  business events.
- Expected workload shape, not only peak request count.
- Reliability, latency, recovery, security, privacy, compliance, residency,
  audit, and cost targets.
- Team skills, delivery deadline, deployment platform, and on-call capacity.

Do not block on exact scale when a reversible baseline is safe. State the
assumption and define the measurement that would reopen the choice.

## Application Archetypes

Choose one primary archetype and any necessary secondary applications.

| Archetype | Dominant concern | Typical shape |
| --- | --- | --- |
| Public content or commerce | Discovery, rendering, performance, content flow | Web framework with server or build-time rendering |
| Authenticated web product | Interaction, navigation, API state, permissions | Modular backend plus SPA or full-stack web framework |
| Server-driven CRUD or operations | Forms, tables, permissions, delivery speed | Server-rendered HTML with progressive enhancement |
| API or integration service | Stable machine contract and domain behavior | API runtime plus authoritative storage as needed |
| Data or AI application | Iteration, model/data access, visualization | Python data UI or API with a dedicated product UI |
| Automation or worker | Repeatable work, integrations, scheduling | CLI, worker, or job with explicit state ownership |
| Mobile or desktop product | Native capabilities, offline state, distribution | Native or cross-platform client plus backend if needed |
| Realtime collaboration or control | Connection state, fan-out, ordering, latency | Realtime transport with explicit consistency model |
| Durable business process | Long waits, retries, approvals, compensation | Workflow engine plus activity workers |
| Event-streaming platform | Replay, independent consumers, ordered streams | Durable log, schemas, consumers, and operational tooling |
| Embedded or systems application | Hardware, memory, timing, safety | Platform-native or systems-language toolchain |

If the requested archetype is unfamiliar, research it. Do not map every
application to the general web profile.

For an AI-enabled archetype, classify the user journey and surrounding product
stack here, then use `ai-stack` for undecided model access, training, inference,
agent, interoperability, retrieval, and AI evaluation layers. Keep the scoped
decisions separate until `design` or the user synthesizes them.

## Selection Axes

Score candidates qualitatively against the axes that matter. Do not manufacture
numeric precision when evidence is absent.

| Axis | Questions |
| --- | --- |
| Functional fit | Does it support the primary journeys and required platform capabilities? |
| Data fit | Does it match transactions, queries, consistency, retention, and recovery? |
| Delivery fit | Can the team ship and test it within the deadline? |
| Operational fit | Who deploys, monitors, upgrades, backs up, restores, and responds? |
| Security fit | Does it support required identity, isolation, encryption, audit, and patching? |
| Scale fit | Which dimension grows, and does the candidate address that dimension? |
| Ecosystem fit | Are required clients, libraries, integrations, and skills mature enough? |
| Cost fit | What are the build, service, staffing, network, storage, and observability costs? |
| Portability fit | Is lock-in acceptable, and what is the exit or migration boundary? |
| Reversibility | Can the choice be replaced incrementally if assumptions change? |

Check maintenance status, licensing, supply-chain posture, current official
support, and known deprecations before adoption.

## Architecture Decisions

### Deployment Boundaries

Default to a monolith or modular monolith when one team, one transactional
model, and one release cadence are sufficient. Introduce independently
deployed services only for a demonstrated domain, ownership, security,
availability, scaling, or release-isolation need.

### User Interface

Choose based on rendering and interaction:

- Server-rendered HTML for content, forms, tables, and simple navigation.
- Progressive enhancement when limited interactivity does not justify a SPA.
- SPA for rich client state with a deliberately separate backend contract.
- Full-stack web framework for integrated routing, rendering, data loading, and
  server execution.
- Native or cross-platform client when device capability, offline behavior, or
  distribution requires it.
- Data UI for exploratory or internal workflows with an execution model the
  team accepts.

Do not let two UI systems own the same DOM subtree or the same routing and
server-state responsibilities without an explicit integration boundary.

### Work Execution

Use the narrowest fitting abstraction:

| Need | Abstraction |
| --- | --- |
| Caller needs an immediate result | Synchronous request |
| Independent work should run later | Task queue and worker |
| Work starts at a known time | One scheduler and an idempotent job |
| Process spans durable steps or long waits | Workflow orchestration |
| Independent systems react to retained facts | Event stream |

Commands request work. Events record facts. Do not substitute an event stream
for an ordinary task queue or a task queue for a durable multi-step workflow.

### Data

- Give every authoritative entity one writable owner.
- Choose storage from access, consistency, lifecycle, and recovery needs.
- Keep caches, search indexes, projections, and analytics rebuildable where
  practical.
- Add a cache only with freshness, invalidation, ownership, and failure rules.
- Separate transactional state from analytical workloads when their needs
  diverge.
- Atomically commit database state and an outbox record when both represent one
  business action. Publish the outbox record later through a retryable relay or
  CDC path, reconcile failures, and keep consumers duplicate-safe.

## Component Admission Test

Admit a component only when all answers are explicit:

1. What current requirement does it satisfy?
2. Why is the current stack or a simpler option insufficient?
3. Who owns it in development and production?
4. How is it tested, secured, observed, upgraded, backed up, and recovered?
5. What failure or overload behavior is acceptable?
6. What does it cost in infrastructure and cognitive load?
7. Can it be deferred, managed, or replaced with a platform-native capability?
8. What measured trigger would add, replace, split, or remove it later?

If these answers are unavailable, mark the component `Deferred`, not
`Required`.

## Decision Record

Return a table with these fields:

| Category | Selection | Status | Rationale | Revisit trigger |
| --- | --- | --- | --- | --- |

Then record:

- architecture, data flow, and control flow;
- explicit assumptions and evidence quality;
- alternatives rejected and their better-fit conditions;
- security, reliability, observability, recovery, and ownership obligations;
- proof of concept, benchmark, migration check, or official-doc verification
  still required;
- incremental implementation slices.
