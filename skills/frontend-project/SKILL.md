---
name: frontend-project
description: "Create or standardize React, TypeScript, and Vite frontends/components with strict config, routes, environment checks, tests, tooling, docs, and candidate manifests. Not for Node backends, other frameworks, Sites, containers, root CI, or stack selection."
---

# Frontend Project

## Help

For `$frontend-project --help` or `$frontend-project -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Create a focused React, TypeScript, and Vite project with deterministic
repo-owned templates and clear standalone versus coordinated ownership.

## Use This Skill For

- New React, TypeScript, and Vite applications.
- Standardizing an existing application already committed to that stack.
- A bounded frontend materialization unit assigned by `scaffold-project`.
- Adding strict type checking, unit tests, linting, and production build
  scripts to that frontend.

## Invocation Scope

Determine scope before generating files:

- `standalone`: own the frontend repository root and its component artifacts.
  Use `gitignore` and `github-workflows` separately for root cross-cutting files.
- `coordinated-candidate`: receive exact assigned paths and exclusions from
  `scaffold-project`; write exact candidates only into the private bundle and
  never modify the target.

In coordinated scope, do not create or modify repository-root `.gitignore`,
README, Makefile, `.github/`, infrastructure, Helm, Dockerfiles, Compose, agent
instructions, or non-frontend code.

## Inputs

- Target component path.
- Package/workspace name.
- Single-line Unicode NFC display name; render it with the context-specific
  Markdown, HTML, and JSON-string escaping contract.
- Exact supported Node, React, React DOM, TypeScript, Vite, plugin, and test
  dependency versions.
- Approved package manager (`npm`, `pnpm`, `yarn`, or `bun`) and exact
  package-manager version.
- Browser/runtime targets and public base path when non-default.
- Approved API origin or proxy behavior when required.
- Public `VITE_*` environment variable names and required/optional status,
  never values.
- Explicit routing, styling, testing, lint, and format profiles. Use `none`
  rather than silently selecting optional tooling.
- Invocation scope and assigned path/exclusion contract.

In coordinated non-interactive mode, fail with missing field paths instead of
choosing versions, package managers, ports, routes, or API origins.

## Required Reads

- Inspect existing package manifests, lockfiles, TypeScript/Vite configuration,
  source, tests, and repository instructions.
- Read `references/project-contract.md` before scaffolding.
- Use the assets in `assets/react-vite/`; do not run `create-vite` or another
  network/native generator.
- In coordinated scope, use
  `scripts/frontend_project.py render --request <request.json> --output
  <private-candidate-directory>` and return its manifest.
- Verify version compatibility and current command behavior against official
  React, Vite, package-manager, and test/lint tool documentation before
  selecting or changing versions.

## Workflow

1. Confirm React, TypeScript, and Vite are already approved.
2. Resolve standalone or coordinated-candidate scope.
3. Collect only missing byte-changing inputs.
4. Inspect for brownfield collisions and preserve existing source.
5. Validate the closed render request and exact assigned/excluded path set.
6. Render the asset placeholders with validated literal values.
7. In standalone scope, write only approved frontend-owned files.
8. In coordinated scope, place candidates under the private bundle and return
   the profile, normalized inputs and digest, exact file digests, modes, and
   bound validation records in a canonical manifest.
9. Validate the candidate manifest, file permissions, digests, JSON, and
   placeholder completeness offline.
10. Run dependency-backed lint, format, typecheck, tests, and build only when
    dependency installation is already available or separately authorized.

## Output Contract

Own these component-level files when required:

- `package.json`
- `index.html`
- `tsconfig.json`, `tsconfig.app.json`, and `tsconfig.node.json`
- `vite.config.ts` and `vitest.config.ts`
- `src/main.tsx`, `src/App.tsx` root layout, styles, and test setup
- `src/router.tsx` only when the approved profile selects React Router
- `src/env.ts` and a names-only `.env.example`
- component unit tests
- component `README.md`
- component-local lint/format scripts, dependencies, and configuration only
  when explicitly selected

Do not generate a lockfile without running the approved package manager. Never
invent or hand-write a lockfile.

Return:

- scope and component path;
- exact versions and package manager used;
- files written or candidate manifest produced, including input and file
  digests;
- scripts for dev, typecheck, test, build, and preview plus only explicitly
  selected lint/format scripts;
- validation run and dependency-backed checks left pending;
- root integration requirements for the coordinator.

## Guardrails

- The supported materialization profile is React, TypeScript, and Vite only.
- No Node backend, Next.js, Remix, Vue, Svelte, Angular, mobile, or Electron
  scaffolding.
- No dependency installation, native generator, dev server, network access, or
  browser launch unless separately authorized.
- No secret, private endpoint, production credential, or machine-local path in
  source or examples.
- Keep API origins configurable and do not enable wildcard production CORS.
- Treat every `VITE_*` variable as public client data. Reject values/defaults
  and secret-like names, including API-key and access-key markers, in reusable
  requests and examples. Match credential markers after removing separators so
  compact forms such as `APIKEY` and `ACCESSKEY` also fail.
- Do not duplicate repository-root CI, ignore, container, infrastructure, or
  deployment ownership.
- In coordinated mode, reject any assigned frontend path outside the supplied
  frontend materialization root or any path not required by the selected
  profile.
- Do not add compatibility wrappers or legacy build paths.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Validation

Prefer:

```bash
python3 frontend-project/scripts/frontend_project.py validate \
  --manifest <candidate-manifest>

# Run only when selected and dependencies are already available:
<package-manager> run lint
<package-manager> run format
<package-manager> run typecheck
<package-manager> run test
<package-manager> run build
```

Report these as pending when dependencies are not installed. JSON parse and
placeholder-completeness checks remain mandatory and offline.
