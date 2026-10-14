# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below to review the intent and maintenance boundary and the
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
  per-capability runtimes, unsupported capability selections outside frontend/core Python, and
  external-service substitutions under an approved component ID;
- rejects unknown or misspelled frontend capability IDs;
- keeps candidate specialist target writes disabled; bootstrap owns only declared
  dependency/environment artifacts after static apply;
- shows the complete tree, operations, merge diffs, validations, and digest;
- rejects instruction files, symlinks, special files, unsupported owners, and
  normalized path collisions;
- treats root paths as brownfield even when adding a greenfield component;
- applies the internally finalized digest under creation-task authority without
  a repeated user approval;
- never starts Agentic SDLC or performs Git, provision,
  deploy, publish, commit, push, or PR actions.

## Manual Runtime Check

In a fresh Codex surface where the skill is installed, exercise
the current positive and negative cases in `trigger-prompts.csv`.
Verify that maintenance negatives do not load the coordinator, natural-language
creation positives select it, and explicit Python creation hands ownership over
once. Verify plan-only cases produce no target/environment mutations. Until observed,
report only metadata and static routing readiness.

Python output cases must distinguish static apply from ready-to-develop status,
complete real lock/environment checks, and preserve authored files on repeat.
