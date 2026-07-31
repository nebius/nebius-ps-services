# Technology Profiles

These profiles are starting points, not universal answers. Re-evaluate them
against `selection-framework.md`, the current repository, and current official
documentation.

## Contents

- [Python Web Framework Choice](#python-web-framework-choice)
- [General Web Application Profile](#general-web-application-profile)
- [Frontend Choice](#frontend-choice)
- [Asynchronous Work And Scheduling](#asynchronous-work-and-scheduling)
- [Event Streaming](#event-streaming)
- [Data And AI Interfaces](#data-and-ai-interfaces)
- [Tooling And Operations](#tooling-and-operations)
- [Official Sources](#official-sources)

## Python Web Framework Choice

Do not equate "Python web application" with "FastAPI application." Choose the
framework shape from the product and integration boundary:

| Requirement | Recommended direction |
| --- | --- |
| API-first service, machine contract, or separately deployed backend | FastAPI with Pydantic, plus explicitly selected persistence and migration tools |
| Server-rendered business application needing integrated forms, authentication, permissions, admin, ORM, and migrations | A batteries-included framework such as Django |
| Existing mature framework with working local conventions | Preserve it unless a measured gap justifies migration |

When choosing Django, prefer its cohesive ORM and migration path instead of
adding SQLAlchemy and Alembic by default. When choosing FastAPI, select the
missing persistence, authentication, admin, forms, and UI responsibilities
explicitly rather than assuming the framework supplies them.

## General Web Application Profile

Start with a modular monolith unless requirements justify another boundary.

| Category | Default profile | Status and condition |
| --- | --- | --- |
| Language | Python | Required only when Python fits the domain, team, runtime, and ecosystem |
| HTTP API | FastAPI with Pydantic | Required for a typed Python HTTP API; otherwise choose the ecosystem-native framework |
| System of record | PostgreSQL | Required for relational transactional state; not for applications without such state |
| Database access | SQLAlchemy 2 | Required when the Python service accesses SQL through SQLAlchemy Core or ORM |
| Schema migrations | Alembic | Required with SQLAlchemy-managed PostgreSQL schema evolution |
| Browser UI | Select from the frontend decision below | Do not install every UI option |
| Background work | None initially | Add a task system only for work outside request or job boundaries |
| Cache | None initially | Add after identifying an ephemeral-state or measured performance need |
| Event stream | None initially | Add only for retained facts, replay, and independent consumers |

Keep domain policy isolated from HTTP, persistence, broker, and UI details.
Run reviewed migrations as an explicit deployment step rather than from every
application replica. Treat tested restore and point-in-time recovery as
operational capabilities, not properties gained merely by choosing PostgreSQL.

## Frontend Choice

Current React guidance recommends starting new React applications with a
framework. It also documents building from scratch with a tool such as Vite
when a framework does not fit. Make that trade-off explicit.

| Requirement | Recommended direction |
| --- | --- |
| Integrated routing, data loading, code splitting, SSR, SSG, or route-level server behavior | A current React framework evaluated against deployment and backend ownership |
| Deliberately separate client-rendered SPA with FastAPI as the only backend | React with TypeScript and Vite |
| Multi-view SPA routing | React Router in the selected library or framework mode |
| API-heavy SPA server state | TanStack Query when its cache and mutation model solve a real need |
| Utility styling and source-owned components | Tailwind CSS and shadcn/ui when the team wants that design-system model |
| Forms, tables, CRUD, and server-owned HTML | Server templates with HTMX as progressive enhancement |
| Data exploration, AI demos, or internal operational tools | Streamlit as a separate application profile |

Do not add React Router or TanStack Query to a page that does not need routing
or client-side server-state coordination. Do not let React and HTMX update the
same DOM subtree. Confirm accessibility, navigation, authentication, browser
support, testing, and deployment for the selected UI.

## Asynchronous Work And Scheduling

### Celery And Brokers

Use Celery for independent Python tasks such as delivery retries, reports, file
processing, batch inference, and integration work. Do not use it merely to make
ordinary code asynchronous.

Use RabbitMQ as the profile's reliability-oriented Celery broker when its
queueing model, acknowledgements, publisher confirms, routing, and operational
cost fit. Redis is a valid stable Celery broker, but select it for a concrete
simplicity or existing-platform reason and evaluate persistence and failure
semantics. Do not use Kafka as this profile's Celery broker; current Celery
documentation labels that transport experimental and without monitoring or
remote-control support.

Tasks must define timeouts, bounded retries, backoff, idempotency, poison-task
handling, and result-retention needs. Broker selection does not provide those
application guarantees automatically.

### Durable Workflows

Use Temporal instead of an ordinary task queue when the business process has
durable multi-step state, long timers, human approvals, compensation, or must
resume at the correct point after failures. Account for workflow determinism,
worker compatibility, visibility, retention, and operational ownership.

### Scheduling

- Use Celery Beat when Celery owns both dispatch and execution. Run one
  authoritative scheduler for a schedule and protect tasks against overlap.
- Use Kubernetes CronJob for independent container jobs when Kubernetes is
  already the deployment platform. Configure concurrency and missed-run
  behavior, and make jobs idempotent because scheduling is approximate.
- Use a platform scheduler when it already owns the job lifecycle and provides
  the required operational controls.

Never schedule the same logical job in more than one system.

## Event Streaming

Kafka and Redpanda occupy the durable event-streaming role. Choose one for a
given stream platform unless a migration or replication design requires both.

Use event streaming only with a concrete need for one or more of:

- multiple independently deployed consumer groups;
- replay or rebuilding derived state;
- streaming analytics, monitoring, feature, or integration pipelines;
- change data capture;
- sustained event flow that needs a durable partitioned log;
- per-key ordering within partitions;
- producers decoupled from the current consumer set.

Avoid it for ordinary background jobs, cron work, one API plus one worker, or
systems without replay and independent-consumer requirements.

Choose Apache Kafka when Kafka behavior or ecosystem integration is a hard
requirement. Consider Redpanda when its Kafka-compatible interface and
operational model fit, but verify every required client, protocol, security
feature, connector, and administrative operation against current compatibility
documentation. Kafka API compatibility is not universal feature equivalence.

Define partition keys, schemas and compatibility, duplicate handling, retry
and dead-letter policy, lag monitoring, retention, access control, replay,
reconciliation, and ownership before adoption. Use a transactional outbox when
a domain change and event intent belong to one business operation: commit the
domain change and outbox record atomically, then publish through a retryable
relay or CDC path. Broker publication is later and may be duplicated, so
monitor and reconcile the relay and keep consumers idempotent.

## Data And AI Interfaces

Use Streamlit for rapid Python data applications, model demonstrations,
exploratory dashboards, and internal tools when its script execution and state
model fit. Treat it as a separate deployment rather than the default frontend
for a customer-facing product requiring precise navigation, accessibility,
design-system control, or complex client architecture.

For production AI systems, select model serving, vector search, object storage,
batch processing, evaluation, and GPU infrastructure only from actual model,
latency, data, scale, security, and cost requirements. Do not infer them from
the presence of the word "AI".

## Tooling And Operations

For the Python profile, prefer repository-consistent tooling. A common current
baseline is `uv` for environments and dependency locking, Ruff for linting and
formatting, and pytest for tests. Use Docker Compose for local dependencies
when containers improve reproducibility and the repository already accepts
that workflow.

Start observability with structured logs, actionable metrics, health signals,
and correlation across boundaries. Use OpenTelemetry when interoperable traces,
metrics, or logs are required. Define dashboards, alerts, sampling, retention,
and sensitive-data redaction instead of assuming instrumentation alone provides
operability.

Prefer managed stateful services when the team does not intend to operate them,
but evaluate cost, data residency, portability, support, backup, restore,
upgrade, and failure ownership.

## Official Sources

Verify volatile behavior again at decision time.

- [FastAPI](https://fastapi.tiangolo.com/)
- [Django documentation](https://docs.djangoproject.com/en/stable/contents/)
- [Pydantic](https://docs.pydantic.dev/latest/)
- [PostgreSQL](https://www.postgresql.org/docs/current/)
- [SQLAlchemy 2](https://docs.sqlalchemy.org/en/20/)
- [Alembic](https://alembic.sqlalchemy.org/en/latest/)
- [React application guidance](https://react.dev/learn/creating-a-react-app)
- [Vite](https://vite.dev/guide/)
- [React Router](https://reactrouter.com/start/framework/installation)
- [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/overview)
- [Tailwind CSS](https://tailwindcss.com/docs/installation)
- [shadcn/ui](https://ui.shadcn.com/docs)
- [HTMX](https://htmx.org/docs/)
- [Streamlit execution model](https://docs.streamlit.io/develop/concepts/architecture)
- [Celery brokers](https://docs.celeryq.dev/en/latest/getting-started/backends-and-brokers/index.html)
- [Celery periodic tasks](https://docs.celeryq.dev/en/latest/userguide/periodic-tasks.html)
- [RabbitMQ reliability](https://www.rabbitmq.com/docs/reliability)
- [Redis](https://redis.io/docs/latest/)
- [Temporal](https://docs.temporal.io/)
- [Kubernetes CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)
- [Apache Kafka documentation](https://kafka.apache.org/documentation/)
- [Redpanda Kafka compatibility](https://docs.redpanda.com/current/develop/kafka-clients/)
- [Debezium outbox event router](https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html)
- [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/)
- [uv](https://docs.astral.sh/uv/)
- [Ruff](https://docs.astral.sh/ruff/)
- [Docker Compose](https://docs.docker.com/compose/)
