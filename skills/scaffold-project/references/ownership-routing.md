# Ownership Routing

Read this reference before assigning materialization units or operations.

| Concern | Content owner |
| --- | --- |
| Root topology, integration docs, aggregate Makefile | `scaffold-project` |
| Python package, tests, local configuration | `python-project` |
| React, TypeScript, Vite package and component tests | `frontend-project` |
| Dockerfile/Containerfile, `.dockerignore`, approved Compose variants, Docker Bake | `container` |
| Terraform root or module files | `terraform` |
| Helm chart package | `helmchart` |
| Root `.github/workflows/*.yml` | `github-workflows` |
| Root `.gitignore` | `gitignore` |
| Explicit helper `.sh` scripts | `shell-scripting` |

## Routing Rules

- Assign ownership per file, not merely per directory. A Dockerfile can be
  owned by `container` inside a Python-owned component root.
- Container-owned Compose and Bake files use only the exact root or component
  filenames accepted by the guarded executor. The container specialist
  defines their runtime and validation contract; the coordinator only
  materializes approved bytes.
- Keep repository-root integration files with the table owner. An explicitly
  assigned component-local Python or Terraform `Makefile`, and a
  Terraform-local `.gitignore`, remain with that materialization specialist.
- Reserve root cross-cutting files before invoking component specialists.
- Give every specialist explicit exclusions for files owned elsewhere.
- Bind every specialist invocation to one candidate-set ID, materialization
  unit (or `null` for a root cross-cutting set), profile, normalized input
  digest, closed manifest, exact operation paths, and validation IDs.
- Require `frontend-project` candidate sets and operations to bind to a
  frontend-owned React/Vite materialization unit. Frontend suffixes at the
  repository root or inside another owner's unit are invalid.
- Allow one physical source unit to serve several logical capabilities.
- Represent each separately deployed process as a runtime unit, even when it
  shares a source unit.
- Do not create source units for external services.
- Fail the whole plan when any required path lacks a supported owner.

## Runtime Skill Classification

- Upstream only: `design`, `app-stack`.
- Candidate producers: the owners in the table above.
- Post-apply audit: `align` in audit-only mode.
- Ambient recovery only: `global-context-management`, `troubleshoot`.
- Authoring only: `skill-creator`, `align-skill`.
- Excluded: Task Implementer, `project-agent-instructions`, Git publication,
  live setup, and every `sdlc-*` skill.

Do not call `linter`, `code-review`, or `apply-security` directly from the
scaffold workflow. Audit-only `align` owns those changed-scope lanes.
