# Safety and Live Validation

Use this before running validation that can mutate files, services, clusters,
cloud resources, package registries, repositories, databases, or production-like
environments.

## Detecting Production Versus Test

Treat an environment as unsafe for live changes unless there is explicit
evidence that it is test, sandbox, disposable, ephemeral, preview, staging with
approval, or otherwise non-production.

Signals that are not enough by themselves:

- A resource name that merely sounds temporary.
- A user's informal confidence without matching environment evidence.
- Read access to credentials.
- A local profile name with no project or account confirmation.

Safer evidence includes:

- User explicitly says the environment is disposable or non-production.
- Config, account, project, subscription, namespace, or cluster metadata marks
  it as sandbox, test, preview, ephemeral, or staging.
- Terraform workspace, Kubernetes context, cloud project, or CI environment is
  documented as non-production.
- The intended command supports a dry run or plan that proves the target before
  mutation.

## Stop Conditions

Stop before:

- Deleting resources or files outside the requested scope.
- Rotating, creating, exposing, or storing credentials.
- Publishing packages, images, charts, releases, or artifacts.
- Applying Terraform, changing Kubernetes resources, altering databases, or
  triggering production CI/CD writes.
- Calling external write APIs.
- Running load tests or cost-incurring jobs.

Continue only when the user explicitly requests the action, the environment is
confirmed non-production where required, and the validation command has an
acceptable blast radius.

## Secret Handling

- Never print, commit, or copy secrets into skill files, reports, examples, or
  templates.
- Use placeholders for tokens, private endpoints, tenant IDs, project IDs, and
  customer-specific values.
- If a skill needs credentials, document the expected environment variable or
  secret manager path without embedding real values.
- If a validation command would expose secrets in output, do not run it. Explain
  the safer alternative.

## Safe Validation Hierarchy

1. Static checks.
2. Local lint, schema, or render checks.
3. Unit tests.
4. Dry runs.
5. Disposable or sandbox integration tests.
6. Live external tests only after test-environment confirmation.

Prefer the earliest level that can verify the claim. Escalate only when the
lower levels cannot answer the question and the next level is safe.

## Domain Rules

Cloud:

- Prefer read-only inspection, plan, validate, or dry-run APIs.
- Confirm account, project, tenant, subscription, and region before mutation.
- Avoid cost-incurring resources unless disposable scope is explicit.

Kubernetes:

- Check the active context and namespace before any command.
- Prefer `kubectl diff`, `helm template`, `helm lint`, and server-side dry-run.
- Do not mutate production clusters without explicit approval and confirmed
  non-production target.

Terraform:

- Prefer `terraform fmt`, `validate`, and `plan`.
- Do not run `apply`, `destroy`, state mutation, or import unless explicitly
  requested and target safety is confirmed.

CI/CD and GitHub:

- Avoid workflow dispatches, branch protection changes, release publication,
  token changes, and destructive repository settings unless explicitly
  requested.
- Prefer local action linting and workflow syntax validation first.

Databases:

- Prefer schema validation, migrations in dry-run mode, or disposable local
  databases.
- Do not write to production databases.

Package Publishing:

- Prefer local build, package, lint, and dry-run publish commands.
- Do not push package, image, chart, or release artifacts unless explicitly
  requested and target registry/repository safety is confirmed.

## Final Reporting

Always report:

- Validation actually run.
- Live tests run or skipped.
- Why skipped live validation was unsafe, infeasible, or unnecessary.
- Remaining behavior that is unverified.
