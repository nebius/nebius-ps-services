# Trigger Prompts

Use these examples to review `app-stack` trigger precision and output quality.
Static examples do not prove runtime activation.

## Should Trigger

```text
Choose the smallest reliable stack for a B2B SaaS application with an API,
authenticated dashboard, PostgreSQL data, email delivery, and nightly reports.
```

```text
Review this existing application's stack and tell me which databases, queues,
caches, and frontend libraries are unnecessary.
```

```text
Should this onboarding process use Celery or Temporal, and what else belongs in
the application stack?
```

```text
We have FastAPI and PostgreSQL. Decide whether we need RabbitMQ, Redis, Kafka,
or none of them for webhook retries and reporting.
```

```text
Select between a React framework, a Vite SPA, HTMX, and Streamlit for these UI
requirements, then coordinate the implementation.
```

```text
Choose a stack for a small authenticated maintenance application with forms,
dashboards, image uploads, PostgreSQL records, and nightly reminders.
```

```text
Use $app-stack to choose and bootstrap the stack for a cross-platform offline
field application with a synchronization backend.
```

```text
Modernize this legacy application's stack without keeping compatibility shims.
```

```text
Choose the stack for this application and deploy it to the production cluster
now, including any paid managed services you recommend.
```

This should select the stack but stop before live mutation until the exact
target, authorization, blast radius, rollback, cost, and required environment
safety are confirmed.

```text
Use $app-stack to add one endpoint to this existing FastAPI and PostgreSQL
service without changing its approved stack.
```

Explicit invocation should be honored, but the response must keep the fixed
stack and route only the narrow requested implementation work.

## Should Not Trigger

```text
Add one FastAPI endpoint to this existing service using its current patterns.
```

The stack is fixed; use the project or Python implementation workflow.

```text
Research the Kafka consumer protocol in depth.
```

Use `research` because the request is due diligence on one technology.

```text
Review this ADR against reliability and data-ownership principles.
```

Use `system-design-rules` because the request is a design checklist review.

```text
Fix this Terraform module for the already-approved application architecture.
```

Use `terraform`; no application-stack decision remains.

```text
Review PR #42 and tell me whether it can merge.
```

Use the PR review workflow.

```text
$sdlc-start run feature-prompt.md
```

Use the explicit Agentic SDLC coordinator.

## Quality Scenarios

For should-trigger prompts, verify that the result:

- classifies the application before naming products;
- states assumptions and decision-changing unknowns;
- recommends a simplest baseline;
- marks components `Required`, `Conditional`, `Deferred`, or `Rejected`;
- does not add queues, caches, workflows, streams, or Kubernetes without a
  requirement;
- considers a cohesive batteries-included server framework when integrated
  forms, authentication, admin, ORM, and migrations reduce the stack;
- provides a revisit trigger for conditional and deferred choices;
- distinguishes commands, schedules, workflows, and events;
- includes data ownership, security, reliability, observability, recovery, and
  operational ownership;
- verifies volatile vendor claims or marks them unverified;
- stays read-only for advice-only prompts;
- stops before unconfirmed live, production, destructive, credential, or paid
  external-service mutation;
- coordinates narrow specialist skills only when implementation is requested.
- returns a scoped stack decision to an active `design` workflow instead of
  recursively handing the full request back to `design`.

## Manual Runtime Check

Test the prompts in a fresh Codex thread where the source skill is installed or
discoverable. Report implicit activation as observed only when the target
surface actually loads `app-stack`. Otherwise report metadata and static eval
readiness.
