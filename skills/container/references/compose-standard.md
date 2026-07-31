# Compose Standard

Use this reference for Compose services, networks, volumes, dependencies,
profiles, development overrides, tests, and approved single-host production
profiles.

## File Conventions

- `compose.yaml`: portable base services and shared contracts.
- `compose.override.yaml`: developer conveniences such as source mounts and hot
  reload.
- `compose.test.yaml`: isolated integration or end-to-end test behavior.
- `compose.production.yaml`: explicit single-host production override after the
  architecture and operational owner approve Compose for production.

Use repository conventions when they already differ. Do not introduce aliases
such as `docker-compose.yml` without an existing contract.

## Service Rules

- Give every service an exact build source, immutable image reference, or both.
- Use explicit networks and named volumes when ownership or isolation matters.
- Keep environment values external and use secret interfaces rather than
  committed values.
- Add dependency health ordering only when the dependency provides a meaningful
  health contract.
- Bind development ports to loopback unless broader access is explicitly
  required.
- Avoid privileged mode, Docker socket mounts, host namespaces, unrestricted
  capabilities, and arbitrary host-path binds.
- Do not expose internal data stores directly in production unless the approved
  operations model requires it.

## Production Override

The production override should normally remove source bind mounts and debug
behavior, use immutable image identity, declare restart behavior, configure
explicit durable storage, narrow published ports, and supply production
resource and security settings from measured evidence.

Compose is an accepted controlled single-host deployment model. Do not
represent it as a multi-host high-availability orchestrator, and do not deploy
it from this skill.

## Validation Lanes

- The typed renderer's Compose validator is a fail-closed local scaffold
  profile. Do not loosen it for general production Compose.
- General Compose auditing may inspect the broader standard and, when Docker
  is available, run capability-detected `docker compose config`.
- Prevent implicit `.env` loading and secret interpolation during audit. Never
  print the fully rendered environment.
- Starting a stack, pulling images, or accessing external dependencies requires
  explicit authorization.

## Official Sources

- [Docker Compose production guidance](https://docs.docker.com/compose/how-tos/production/)
- [Docker Compose file reference](https://docs.docker.com/reference/compose-file/)
- [Docker Compose trust model](https://docs.docker.com/compose/trust-model/)
