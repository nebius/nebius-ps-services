# Frontend Project

`frontend-project` creates and standardizes React, TypeScript, and Vite
applications. It uses deterministic repo-owned templates rather than invoking
a network generator. Coordinated mode has an executable offline producer that
returns a digest-bound candidate manifest for `scaffold-project`. External
requests and manifests reject duplicate object keys and non-standard numeric
literals before validation. Generated TypeScript includes Vite client types,
and display names are context-escaped before entering Markdown, HTML, or source
templates. Render requests accept only `npm`, `pnpm`, `yarn`, or `bun`, and
public environment names reject API-key and access-key markers as client-secret
risks, including compact spellings without underscores.

## Scopes

- `standalone`: a frontend repository or existing frontend root.
- `coordinated-candidate`: exact candidate files for a component assigned by
  `scaffold-project`; the skill never writes the target.

## Ownership

This skill owns package metadata, TypeScript/Vite configuration, the frontend
entrypoint/root layout/optional router shell, plain-CSS baseline, public
environment schema, tests, explicitly assigned tooling, and component
documentation. Root CI, ignore rules, containers, infrastructure, deployment,
and agent instructions stay with their specialist owners.

## Files

- `SKILL.md`: workflow and ownership contract.
- `references/project-contract.md`: inputs, placeholders, and validation.
- `assets/react-vite/`: reusable starter files.
- `scripts/frontend_project.py`: closed deterministic candidate renderer and
  validator.
- `scripts/test_frontend_project.py`: offline contract tests.
- `evals/trigger-prompts.csv`: trigger and boundary cases.
- `evals/process-cases.md`: supplemental workflow and quality cases.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
