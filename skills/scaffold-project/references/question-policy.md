# Question Policy

Ask one concise batch containing only missing answers that change repository
structure or candidate bytes.

## Ask When Missing

- Target directory and plan/add/apply intent.
- Architecture approval reference.
- Repository shape and required materialization boundaries.
- Component paths and package/module/workspace identities.
- Runtime or deployment target when it changes generated files.
- Required CI, Terraform, Helm, container, Compose, root documentation, or
  helper-script artifacts.
- Exact language, framework, runtime, base-image, dependency, package-manager,
  provider, chart, and service versions consumed by templates.
- Whether optional Git initialization, dependency installation, network use,
  or external validation should remain reported only. These remain disabled
  unless separately authorized.

## Do Not Ask

- Technology choices already fixed by the approved architecture.
- Conditional, deferred, or rejected technologies.
- Formatting already established by repository instructions.
- Values discoverable from existing manifests or nearby conventions.
- Feature and business-logic decisions outside scaffolding.

## Non-Interactive Mode

Return a stable list of missing JSON field paths and stop. Do not select a
technology, version, package manager, base image, port, health endpoint,
backend, provider, service image, or deployment model by guessing.
