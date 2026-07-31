# Layout Patterns

Use the smallest pattern justified by the approved component graph.

## Single Component

```text
project/
|-- src/
|-- tests/
|-- README.md
`-- .gitignore
```

Use the language specialist in standalone scope when no cross-language or
cross-platform composition is needed. Do not invoke `scaffold-project` merely
to wrap a single specialist.

## Modular Monolith

```text
project/
|-- apps/
|   |-- backend/
|   `-- web/
|-- infra/
|   `-- terraform/
|-- deploy/
|   `-- helm/
|-- .github/
|   `-- workflows/
|-- README.md
`-- .gitignore
```

API and background worker capabilities may share `apps/backend` while retaining
separate runtime units.

## Multi-Component Repository

```text
project/
|-- apps/
|-- libraries/
|-- infra/
|-- deploy/
|-- docs/
|-- .github/
|-- README.md
`-- .gitignore
```

Use this only when the approved design contains independently owned source or
deployment units. Do not turn every technology or external dependency into a
top-level directory.

## Path Naming

- Keep display name, directory name, distribution/package name, import/module
  name, and workspace name as separate explicit fields.
- Prefer stable component nouns such as `backend`, `web`, `worker`, or `cli`
  only when they reflect approved boundaries.
- Do not create empty directories; Git does not preserve them.
- Do not create root `LICENSE`, `CHANGELOG.md`, Makefile, or editor configuration
  unless the approved lifecycle requires them.
