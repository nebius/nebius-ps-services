# Dependency Management with uv

Load this reference for new scaffolds, dependency changes, package-manager
migrations, lockfile failures, library lower-bound checks, or custom indexes and
sources.

## Select One Authority

- Use the uv project workflow for a new scaffold.
- Preserve an existing project's package manager and lockfiles unless the user
  explicitly requests migration to uv.
- After an authorized migration, remove obsolete install and lock authorities.
  Do not keep pip, Poetry, or multiple lock paths as compatibility shims unless
  the user explicitly requires them.
- Keep direct dependency intent, supported Python, dependency groups, consumer
  extras, indexes, and sources in `pyproject.toml`.
- Generate `uv.lock` with uv, commit it, review it with `pyproject.toml`, and
  never hand-edit or template it. The file is a universal resolved graph and
  may contain alternatives selected by platform or Python markers.
- Generate `requirements.txt` or `pylock.toml` only as a derived export for an
  external consumer. Do not treat an export as another project authority.

In `coordinated-candidate` scope, do not run uv, create a lock candidate, or add
a `uv.lock` template. Return dependency resolution and lock generation as a
pending post-integration step. The coordinator may run it only through a
separately authorized workflow after final root integration.

## Inspect the Installed CLI

Before using version-sensitive options, inspect the available uv release and
command help:

```bash
uv self version
uv <command> --help
uv help <command>
```

The `--help` form is concise; `uv help <command>` provides long-form command
documentation. Repeat `-v` as `-vv` when resolver diagnostics need more detail.

## Assign Dependency Roles

- Put dependencies required by the selected application or package profile in
  `[project].dependencies`.
- Put development-only tools in `[dependency-groups].dev`. The dev group is
  part of the contributor environment and is not a package extra.
- Put a dependency in `[project.optional-dependencies]` only when package
  consumers should explicitly install that feature as an extra.
- Add only the selected profiles. Do not make Typer, Pydantic, Rich, FastAPI,
  Streamlit, or ML packages unconditional when the project does not use them.

For a deployable application, the committed `uv.lock` is the reviewed
deployment graph. Validate deployment and CI with that graph.

For a published library, consumers resolve the compatibility ranges from
`pyproject.toml`; the committed lock still gives contributors and normal CI a
reproducible graph. Separately test declared lower bounds in a disposable CI
checkout:

```bash
uv lock --upgrade --resolution lowest-direct --no-sources
uv sync --locked --no-sources
uv run --locked --no-sources pytest
```

The isolated `--upgrade` is required here so an existing latest-resolution
lock does not remain a resolver preference. `--no-sources` verifies the
publishable project metadata without workspace or `[tool.uv.sources]`
overrides. This lane intentionally creates an alternate lowest-direct lock and
environment. Never commit, publish, or reuse its rewritten `uv.lock` as the
normal latest-resolution lock, and never use this broad re-resolution for an
ordinary scoped dependency change.

## Make a Scoped Dependency Change

After the user authorizes a dependency change, use the role-specific command
and avoid syncing until the intended metadata and resolution can be reviewed:

```bash
uv add --no-sync <runtime-package>
uv add --dev --no-sync <development-package>
uv add --optional <extra> --no-sync <consumer-extra-package>
uv remove --no-sync <runtime-package>
uv remove --dev --no-sync <development-package>
uv remove --optional <extra> --no-sync <consumer-extra-package>

git diff -- pyproject.toml uv.lock
uv lock --check
uv sync --locked
uv run --locked pytest
```

`--no-sync` still permits dependency resolution and updates to
`pyproject.toml` and `uv.lock`; it only skips environment synchronization.
Review both files before syncing.

Use `--locked` for CI and verification. It rejects a missing or stale lockfile
instead of rewriting it. Do not substitute `--frozen`: it skips freshness
checking, and mutation commands such as `uv add --frozen` can change
`pyproject.toml` without resolving a matching lockfile.

Operate on uv's project-owned environment. Do not use `--active` by default;
it can select an unrelated activated environment.

## Upgrade One Package

Preview the scoped resolution before writing repository files:

```bash
uv lock --dry-run --upgrade-package <package>
uv lock --upgrade-package <package>
git diff -- pyproject.toml uv.lock
uv lock --check
uv sync --locked
uv run --locked pytest
```

`--dry-run` avoids repository and environment writes, but dependency resolution
may still contact configured package indexes. Outside the disposable library
lower-bound lane above, do not replace a package-scoped upgrade with broad
`uv lock --upgrade` unless the user explicitly requests a full dependency
refresh.

## Trust Indexes and Sources

- Keep uv's default `first-index` strategy so a package name is resolved from
  the first configured index that provides it. Do not enable an unsafe index
  strategy to make a conflict disappear.
- Give additional indexes stable names. Set `explicit = true` when an index
  should serve only packages bound to it through `[tool.uv.sources]`.
- Store only credential-free index URLs in project files. Supply credentials
  through supported environment variables, a credential provider, or other
  approved external secret storage.
- Prefer immutable, reviewed source references. Pin Git dependencies to a
  reviewed commit when immutability matters; use a tag only when the source
  repository enforces immutable release tags. Do not rely on a moving branch.
- Treat index, source, or credential changes as security-sensitive. Review
  `pyproject.toml` and `uv.lock` together and do not print credential-bearing
  URLs in diagnostics.

Use credential-free, explicit bindings such as:

```toml
[[tool.uv.index]]
name = "vendor"
url = "https://packages.example.com/simple"
explicit = true

[tool.uv.sources]
some-package = { index = "vendor" }
```

For this example, credentials are supplied externally with
`UV_INDEX_VENDOR_USERNAME` and `UV_INDEX_VENDOR_PASSWORD` or another approved
provider; they are never added to the URL.

## Handle Resolution Failures

Preserve uv's complete resolver explanation, adding `-v` or `-vv` only when
needed. Identify the incompatible requirements, Python markers, source, or
index before changing intent.

Do not silently:

- widen supported Python versions
- weaken or delete compatibility bounds
- use `--frozen` to bypass lock freshness
- switch to an unsafe index strategy
- move credentials into project files
- perform a broad upgrade outside the isolated library lower-bound lane
- hand-edit `uv.lock`

If the requested graph cannot resolve under the declared contract, report the
conflict and the smallest explicit decision needed from the user.

## Official References

- [Project files and the universal lockfile](https://docs.astral.sh/uv/concepts/projects/layout/)
- [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [Managing dependency groups, extras, indexes, and sources](https://docs.astral.sh/uv/concepts/projects/dependencies/)
- [Resolution strategies and library lower bounds](https://docs.astral.sh/uv/concepts/resolution/)
- [Package indexes and authentication](https://docs.astral.sh/uv/concepts/indexes/)
- [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- [Getting help with uv](https://docs.astral.sh/uv/getting-started/help/)
