# Scaffold Contract

Read this reference before creating or consuming a scaffold bundle.

## Bundle Layout

```text
<private-bundle>/
|-- manifest.draft.json
|-- candidates/
|-- payloads/
|   `-- <sha256>
|-- manifest.json
|-- journal.json
`-- backups/
    `-- <before-sha256>
```

The bundle is private execution state, not project truth. Keep it outside both
the target and every Git worktree, with directory mode `0700` and files
`0600`. Finalization and every later command reject a symlinked bundle,
repository-contained bundle, or relaxed bundle permissions.

## Draft Contract

`manifest.draft.json` uses the closed
`scaffold-plan.schema.json` contract. Unknown fields and schema versions fail.

Core objects:

- `project`: name, repository shape, and architecture approval.
- `capabilities`: logical API, worker, UI, CLI, library, infrastructure, or
  deployment responsibilities. App-stack-backed capabilities also carry the
  exact approved technology object.
- `materialization_units`: physical source or configuration roots with one
  specialist owner. App-stack-backed units declare the canonical technology
  names they materialize.
- `runtime_units`: independently started or deployed processes that reference
  both one logical capability and one of that capability's materialization
  units.
- `external_services`: databases, caches, queues, or managed services that do
  not create source directories.
- `candidate_sets`: specialist, unit, profile, normalized-input digest,
  candidate-manifest digest, exact operation paths, and validation IDs.
- `operations`: exact candidate files and intended action.
- `validations`: candidate-set-bound checks and external-evidence status.
- `execution`: explicit capabilities; schema v2 requires every external or hidden
  action to remain false.
- `safety.reserved_paths`: additional instruction or protected paths.

Every candidate operation contains:

```json
{
  "path": "apps/backend/pyproject.toml",
  "action": "create",
  "owner": "python-project",
  "materialization_unit_id": "backend",
  "candidate_set_id": "python-backend",
  "candidate": "candidates/python-project/python-backend/files/pyproject.toml",
  "mode": "0644"
}
```

Every candidate set contains:

```json
{
  "id": "python-backend",
  "owner": "python-project",
  "materialization_unit_id": "backend",
  "profile": "python-package",
  "input_sha256": "<sha256>",
  "manifest": "candidates/python-project/python-backend/manifest.json",
  "manifest_sha256": "<sha256>",
  "operation_paths": ["apps/backend/pyproject.toml"],
  "validation_ids": ["python-backend:static"]
}
```

The manifest uses `candidate-manifest.schema.json` and records the same
identity plus the normalized producer inputs, their recomputed digest, exact
file paths, candidate-relative paths, modes, content digests, and validation
records. Candidate-phase validation must already be `passed` and offline.
Dependency-backed post-apply validation may remain `pending` or `not-run` but
stays visible in the approved digest.

An operation inside a materialization-unit path must name that unit. Every
required unit must contain at least one operation owned by its specialist.
Cross-cutting root files use `null`; a container-owned Dockerfile inside a
component names that component's unit while retaining `container` as
its file owner. Materialization owners are limited to Python, React/Vite,
Terraform, Helm, and shell contracts with matching language/framework values.
Every operation references a candidate set with the same owner and unit.
Frontend candidate sets require a non-null frontend-owned React/Vite unit, and
every frontend path must remain strictly beneath that unit root.
Every operation path must match its owner's positive artifact contract;
unknown extensions and extensionless source scripts fail closed. An unsupported
artifact requires a new specialist contract instead of relabeling the file.
Container artifacts accept Dockerfile and Containerfile variants plus
`.dockerignore`; root or materialization-unit roots may also contain the exact
Compose base/override/production/test filenames and Docker Bake HCL or JSON
filenames documented by the container specialist. Other Compose aliases and
nested integration files fail closed.
Exact nonstandard filenames documented by a specialist are accepted only at
their contracted scope: root `.pre-commit-config.yaml` for Python automation,
or owner-bound component files such as Python/Terraform `Makefile`,
Terraform-local `.gitignore`, and `terraform.tfvars.example`.

Allowed actions are `create`, `semantic_merge`, and `unchanged`. In schema v2,
`semantic_merge` is limited to `.gitignore`, `README.md`, and `Makefile`; its
candidate must preserve every original byte and append a non-empty suffix
without changing the mode. `.gitignore` suffixes accept only unique reviewed
baseline/add-on rules. README and Makefile suffixes require one matching named
marker block. File modes are limited to `0644` and `0755`.

## Status Rules

- `required`: must reference at least one materialization unit.
- `conditional`: must have a trigger and cannot reference a materialization
  unit until its trigger is proven.
- `deferred` and `rejected`: cannot reference materialization units.
- Every materialization unit must be referenced by a required capability.
- Runtime units reference materialization units; they do not create duplicate
  source ownership.

## Architecture Approval

Use:

- `direct-user` when the current user explicitly approves the supplied
  architecture; or
- `approved-artifact` with one or more absolute or resolvable source paths and
  their SHA-256 digests.

An app-stack handoff is an optional approved architecture source using schema
version 2 of the closed logical contract in
`../../app-stack/references/scaffold-handoff.schema.json`. Its digest is bound
into the executable manifest. Every component declares a closed
`component_class` and canonical `technology.name`; it supplies no repository
paths or owners.

Finalization records source identity and apply rejects later drift. Do not store
prompt transcripts or confidential architecture content in the bundle.

The Python validator is the executable authority for closed fields, graph
relationships, positive artifact ownership, Unicode NFC normalization, and
filesystem preconditions. The JSON Schema mirrors structural fields for
editor/offline tooling. Standard JSON Schema cannot assert Unicode
normalization, so its `relativePath` definition documents NFC as a runtime-only
invariant; cross-object, normalization, and filesystem invariants remain
fail-closed in the Python validator. Finalization also rejects any
repo-template placeholder matching `{{UPPER_SNAKE_CASE}}`.

Every external JSON document is decoded as UTF-8 and parsed fail-closed.
Duplicate object keys and non-standard numeric literals such as `NaN` or
infinity are rejected before structural or digest validation.

Every required app-stack component must be represented. For each represented
component, the scaffold validator compares the logical ID, status, and exact
technology object. Required materialized components must bind their canonical
technology name and language to every referenced materialization unit. Each
runtime unit carries `capability_id`; every runtime for that capability must
match the approved runtime, which preserves API and worker separation even
when they share a source unit. Component and external-service kinds must match.
External services must retain their canonical selected technology.
Non-frontend capability selections are rejected until their specialist owns a
closed candidate-input binding. For an app-stack-approved React/Vite component,
the validator additionally compares the package manager, exact version map,
materialization-unit binding, declared
routing/styling/testing/public-environment/lint/format selections, and frontend
producer inputs. Any required frontend component outside the supported
React/Vite profile, or any unknown frontend capability ID, fails rather than
disappearing from the plan. A digest-valid candidate with different approved
technology or capability inputs still fails finalization.

## Final Manifest

`finalize`:

1. validates the draft and candidate paths;
2. verifies architecture sources and any logical app-stack handoff;
3. verifies candidate-set manifest digests, file bindings, and validation
   records;
4. copies exact candidates into content-addressed payloads;
5. records target, directory, file, type, mode, byte-size, device, and inode
   preconditions;
6. sorts operations by normalized path;
7. computes `bundle_digest` from canonical UTF-8 JSON with sorted keys, compact
   separators, NFC-normalized strings, no NaN values, and a trailing newline.

The saved `manifest.json` is readable indented JSON, but its digest is computed
from the canonical form with `bundle_digest` omitted.
Validation rechecks each action/precondition/payload relationship, including
semantic-merge prefix and marker invariants. Apply uses that exact validated
in-memory manifest rather than reopening mutable manifest bytes.

## Candidate Handoff

A coordinated specialist receives:

- assigned materialization unit and exact path set;
- approved language/framework/runtime/version inputs;
- names and package identities;
- root ownership and exclusion map;
- private candidate directory;
- required validation evidence.

It returns candidate files, structured profile/input provenance, proposed
actions, and validation requirements in the closed candidate manifest. It
never writes the target or alters the plan digest.

## Sanitized Audit Copy

When requested, create a separate `docs/scaffold-plan.json` containing only the
logical graph, relative paths, owners, statuses, and non-sensitive validation
summary. Exclude absolute paths, payloads, before-state metadata, journals,
backups, prompts, and secrets.
