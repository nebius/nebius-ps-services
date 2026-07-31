# Trigger Prompts

Use these examples to review the explicit-only boundary and output quality.
Static examples do not prove runtime activation.

## Should Trigger

```text
Use $scaffold-project to plan a new repository from this approved component
graph. Do not apply it yet.
```

```text
$scaffold-project add the approved React/Vite component to this existing
Python monorepo. Preserve every existing file and show brownfield merges first.
```

```text
Use $scaffold-project to finalize this reviewed private scaffold bundle, then
apply exactly digest <sha256> to the selected disposable target.
```

## Should Not Trigger

```text
Choose whether this application should use React or server-rendered HTML.
```

Use `app-stack`; the technology decision is incomplete.

```text
Create a Python CLI project with Typer.
```

Use `python-project` directly.

```text
Add a Dockerfile to this established service.
```

Use `container` directly.

```text
Implement the next product feature in this repository.
```

Use the normal implementation workflow.

```text
$sdlc-start run feature-prompt.md
```

Use the explicit Agentic SDLC coordinator without invoking this skill.

## Quality Scenarios

For should-trigger prompts, verify that the result:

- consumes an approved architecture without selecting technologies;
- requires every app-stack-approved component to retain its status and exact
  technology binding, including external-service selections;
- separates logical capabilities, materialization units, and runtime units;
- materializes only required items;
- assigns one supported content owner per normalized path;
- binds operations and validations to digest-checked candidate manifests;
- rejects schema-v1 bundles and frontend artifacts outside their assigned
  frontend root;
- rejects kind drift, mixed technology/language across assigned units, mixed
  per-capability runtimes, unsupported non-frontend capability selections, and
  external-service substitutions under an approved component ID;
- rejects unknown or misspelled frontend capability IDs;
- keeps all specialist target writes disabled;
- shows the complete tree, operations, merge diffs, validations, and digest;
- rejects instruction files, symlinks, special files, unsupported owners, and
  normalized path collisions;
- treats root paths as brownfield even when adding a greenfield component;
- applies only after exact digest approval;
- never starts Agentic SDLC or performs Git, network, install, provision,
  deploy, publish, commit, push, or PR actions.

## Manual Runtime Check

In a fresh Codex surface where the skill is installed, verify that ordinary
scaffolding prompts do not implicitly load it and explicit
`$scaffold-project` prompts do. Until observed, report only metadata and static
trigger readiness.
