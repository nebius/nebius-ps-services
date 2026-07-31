# Scaffold Profiles

Read this reference before using the retained Python or React/Vite assets.
These templates and the closed Compose validator are optional scaffold
profiles, not universal container-generation rules.

## Python Template Inputs

- `build_image` and `runtime_image`: approved exact image references.
- `package_source_paths`: exact normalized relative paths required to build the
  wheel. Each path is copied to the same relative destination so directory
  nodes such as `src/` retain their build-context topology.
- `lockfile`: an existing reviewed `.lock` or `.txt` requirements-format lock
  that pins
  runtime and build-system dependencies and supplies hashes for
  `pip --require-hashes`.
- `distribution_name`: the exact package distribution name.
- `project_version`: an approved non-fallback canonical `MAJOR.MINOR.PATCH`
  SemVer release. The typed renderer
  derives the distribution-scoped
  `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>` variable.
- `runtime_uid` and `runtime_gid`: approved non-root numeric identities.
- `runtime_port`: documented application port.
- `runtime_command`: an exec-form JSON string array.

The template expects a PEP 517-compatible Python project that can build a wheel
without secrets. The hash lock must include the build backend and other
build-system requirements because the project wheel uses
`--no-build-isolation`. The lock is materialized into dependency wheels before
the project wheel is built, and the runtime install is offline from those
wheels. If the lock is absent or incomplete, stop; do not resolve dependencies
or fabricate a lock. If the project needs private indexes or build secrets,
stop and design a separately authorized BuildKit secret flow.

The approved project version is a non-overridable literal in the image
candidate. This prevents setuptools-scm from silently using a fallback when
`.git` is excluded from the build context and prevents a later build argument
from changing the digest-approved package identity.

## React/Vite Template Inputs

- `build_image` and `runtime_image`: approved exact references.
- `package_manager`: `npm`, `pnpm`, or `yarn`. The typed renderer owns the
  reviewed frozen-install and build recipes; request text never supplies shell
  command fragments.
- `lockfile`: the existing reviewed lockfile for that package manager.
  Accepted pairs are `npm` with `package-lock.json` or
  `npm-shrinkwrap.json`, `pnpm` with `pnpm-lock.yaml`, and `yarn` with
  `yarn.lock`.
- `build_output_path` and `static_root`: verified output/runtime paths.
- `runtime_uid`, `runtime_port`, and `runtime_command`.

Require an existing reviewed lockfile before using a frozen install. Do not
substitute `vite preview` as a production server.

## Deployment Context

Keep the image artifact separate from the production deployment platform:

- For local development, automated test, or CI, create `compose.yaml` only
  when explicitly required.
- For a production-capable image, create a runtime-hardened Dockerfile and
  `.dockerignore`.
- For approved single-host production Compose, do not reuse the local scaffold
  template or closed validator. Use `compose-standard.md` and author the
  production override from the approved runtime and operations contract.
- For an approved Kubernetes deployment, return image and runtime integration
  requirements to `helmchart`. Do not create Kubernetes or Helm files here.
- For another managed container platform, return deployment requirements to
  its owner. Do not invent platform files.

Docker officially supports Compose for single-host production. This retained
template intentionally remains limited to local development, test, and CI so
its closed schema does not silently become a production policy.

Do not default production containers to Kubernetes. Select Kubernetes only
when requirements such as multi-host resilience, controlled rollouts,
self-healing, autoscaling, shared policy, or platform standardization justify
its operational complexity and ownership.

## Compose Inputs

Render `compose.yaml.template` only for local development, test, or CI, with
complete service definitions. The renderer does not produce
`compose.production.yaml`. Each service must declare only approved:

- relative build context or exact image;
- command and port mappings;
- environment variable names or exact external/required-value references;
- named volumes;
- health checks;
- `depends_on` conditions.

Use `condition: service_healthy` only when the dependency has a meaningful
health check. Keep secret values outside the file.

The bounded V1 Compose validator rejects:

- every top-level, service, build, health-check, or dependency key outside the
  closed local profile. Newly added Compose fields fail closed until this
  contract and validator explicitly review them;
- duplicate YAML keys, legacy `version`, includes, lifecycle hooks,
  host-reading secret/config files, API-socket access, and providers;
- privileged mode, host networking or namespaces, devices, added
  capabilities, Docker socket mounts, and absolute host bind paths;
- floating or missing image tags, malformed registry ports, every literal or
  defaulted environment value, and root user declarations. Values may use only
  exact external references such as `${LOG_LEVEL}` or required-value
  expressions such as `${API_TOKEN:?required}`;
- interpolated commands, users, paths, and ports. V1 users are explicit
  non-root numeric identities, and published local ports bind explicitly to
  `127.0.0.1`;
- string-form commands and health checks, health tests other than `CMD`,
  `CMD-SHELL`, or exact disabled `NONE`, unknown dependencies, and
  `service_healthy` dependencies without an enabled health check. Health timing
  fields must use the Compose duration units `us`, `ms`, `s`, `m`, or `h`.
- falsey or non-list `security_opt` and service `volumes` values, including
  nulls and mappings that could otherwise bypass closed-profile type checks.

Every service must declare `image`, `build`, or both. When both are present,
the exact image reference names the locally built result. Named volume
definitions remain empty in V1, and build contexts, Dockerfile paths, and bind
sources remain normalized and relative. Long-form volumes accept only
`type`, `source`, `target`, and optional `read_only`; propagation and other
host-control settings are excluded. Short-form volume targets are parsed and
validated completely, named sources must be declared at the top level, and
relative bind modes may only be omitted or read-only.

## Template Rules

- Render Dockerfiles only through `scripts/render_container_asset.py`. The
  helper accepts typed JSON values, owns all shell recipes, emits to standard
  output, rejects duplicate/unknown/missing fields, and fails on unresolved
  placeholders.
- Do not directly replace Dockerfile placeholders or interpolate request text.
- Compose generation may use the YAML skeleton, but the complete rendered file
  must pass the helper's duplicate-key parser and semantic validator.
- Keep build contexts relative to the Compose file.
- Prefer multi-stage builds and copy only runtime artifacts.
- Use `COPY`, not remote `ADD`.
- Keep runtime images minimal and processes non-root.
- Add writable directories explicitly rather than disabling a read-only root
  filesystem blindly.
- Never include `.git`, local environments, caches, coverage, credentials, or
  secret-bearing environment files in a build context.

## Validation

Offline:

- confirm no placeholders remain;
- render Dockerfiles through the typed helper;
- parse Compose YAML with duplicate-key rejection and run the bounded semantic
  validator;
- check that every `COPY` source exists in the selected context;
- confirm the Python hash lock contains all build/runtime requirements and
  hashes, and confirm the approved project version is the intended canonical
  SemVer release rather than `0+unknown`;
- check that runtime command, user, port, and health assumptions match the
  application contract.

Separately authorized:

- `docker build --check --build-arg
  "BUILDKIT_DOCKERFILE_CHECK=error=true"` so build-check warnings fail;
- `docker compose config --quiet`;
- disposable image build and runtime health smoke;
- vulnerability/SBOM tooling already used by the repository.

## Official Sources

- [Docker Compose use cases](https://docs.docker.com/compose/intro/features-uses/)
  documents development, automated test, and single-host production use.
- [Docker Compose in production](https://docs.docker.com/compose/how-tos/production/)
  documents the production-specific changes needed for a single-server path.
- [Docker Compose trust model](https://docs.docker.com/compose/trust-model/)
  documents the host access and privilege implications of Compose fields.
- [Docker build checks](https://docs.docker.com/build/checks/) documents
  error-enabled Dockerfile checking.
- [setuptools-scm container guidance](https://setuptools-scm.readthedocs.io/en/stable/usage/)
  documents distribution-scoped pretend-version variables for builds without
  copied Git metadata.
- [Kubernetes overview](https://kubernetes.io/docs/concepts/overview/)
  documents rollout, self-healing, scaling, service discovery, and
  configuration capabilities.
- [Kubernetes production environment](https://kubernetes.io/docs/setup/production-environment/)
  documents the availability, scaling, security, and operational planning
  required for production clusters.
