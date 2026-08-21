# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below to review the explicit-only boundary and the
assertions here to review output quality. Static validation does not prove
runtime activation.

## Quality Scenarios

For canonical positive cases, verify that the result:

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

In a fresh Codex surface where the skill is installed, exercise
`scaffold-positive-01` through `scaffold-positive-03` and
`scaffold-negative-01` through `scaffold-negative-05`. Verify that negative
rows do not implicitly load it and explicit positive rows do. Until observed,
report only metadata and static routing readiness.
