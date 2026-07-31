# React Vite Project Contract

Read this reference before rendering assets or consuming a coordinated
candidate manifest.

## Closed Render Request

`scripts/frontend_project.py render` accepts schema version 1 with:

- `candidate_set_id`, `component_id`, `materialization_unit_id`, and the
  `react-vite` profile;
- a normalized repository-relative component root;
- the exact assigned and excluded repository paths;
- package/display names, package manager, exact manager version, and Node
  range;
- exact versions for every selected dependency;
- routing, styling, testing, public-environment, lint, and format selections.

The base profile owns:

```text
.env.example
README.md
index.html
package.json
src/App.test.tsx
src/App.tsx
src/env.ts
src/main.tsx
src/styles.css
src/test/setup.ts
tsconfig.app.json
tsconfig.json
tsconfig.node.json
vite.config.ts
vitest.config.ts
```

The assigned path set must equal the base set under the component root plus
only the files activated by these explicit profiles:

- `routing.profile=react-router`: `src/router.tsx`;
- `lint.profile=oxlint`: `.oxlintrc.json`;
- `format.profile=prettier`: `.prettierignore` and `.prettierrc.json`.

Use `none` with a `null` version for unselected optional profiles. The selected
profile requires its exact version in both the capability and versions maps.
Candidate-set IDs use lowercase safe identifiers. Package and manager versions
must be exact semantic versions; tags, ranges, URLs, aliases, and local paths
fail closed. Node ranges accept only the bounded semver-range character set and
are JSON-encoded before template insertion. Unknown fields, paths,
dependencies, profiles, and floating versions fail closed. External request
and manifest documents also reject duplicate object keys and non-standard
numeric literals such as `NaN` or infinity before structural validation.

## Safe Literal Inputs

- Package names must be safe npm-style unscoped or scoped names.
- The package-manager value must be one of `npm`, `pnpm`, `yarn`, or `bun`.
  Arbitrary command tokens are not package managers and fail closed.
- `DISPLAY_NAME` must be Unicode NFC, single-line human text with no control
  characters. Derive context-specific values:
  - Markdown headings HTML-escape `&`, `<`, and `>` before escaping Markdown
    punctuation.
  - HTML `&`, `<`, and `>` are escaped.
  - TSX expressions use a JSON string literal with Unicode preserved.
- Exact versions must not use ranges, `latest`, workspace/file references, or
  other floating selectors.

Never place raw display-name input directly into Markdown, HTML, TSX, or
JavaScript contexts. Do not leave `{{UPPER_SNAKE_CASE}}` placeholders in a
candidate.

## Runtime Shell

- `src/main.tsx` mounts through `createRoot` from `react-dom/client`.
- `src/App.tsx` is the root application layout and remains free of product
  business behavior.
- With `routing.profile=none`, the entrypoint renders `App` directly.
- With `routing.profile=react-router`, create one browser router outside the
  React tree and render it through `RouterProvider`; `src/router.tsx` owns the
  minimal `/` route shell.
- `dev`, `build`, and `preview` use Vite.
- `build` and `typecheck` use the TypeScript project references.
- `tsconfig.app.json` includes `vite/client` types so `import.meta.env` is
  typed by Vite.
- `test` uses Vitest with jsdom and React Testing Library.
- Oxlint and Prettier scripts/configuration exist only when explicitly
  selected.

Current reference evidence:

- Vite environment behavior:
  <https://vite.dev/guide/env-and-mode>
- React Router library installation:
  <https://reactrouter.com/start/data/installation>
- Oxlint configuration:
  <https://oxc.rs/docs/guide/usage/linter/quickstart>
- Prettier installation:
  <https://prettier.io/docs/install>

## Public Environment Contract

Accept only unique uppercase names matching `VITE_[A-Z][A-Z0-9_]*` plus a
required/optional boolean. Reject value/default/example fields and names that
suggest secrets, tokens, passwords, private keys, API keys, access keys, or
credentials. Compare these markers after removing underscores so compact forms
such as `APIKEY` and `ACCESSKEY` cannot bypass the rule.

Generate:

- `.env.example` with sorted `NAME=` lines and no values;
- `src/env.ts` with an allowlisted, typed, immutable public environment object;
- fail-fast runtime validation for missing required names.

Vite exposes `VITE_*` values to client code. They are configuration, never
secrets. Secret values belong behind a server boundary and must not appear in
requests, candidates, manifests, examples, or logs.

## Candidate Manifest

The coordinated renderer writes only within the supplied empty private `0700`
candidate-set directory:

```text
manifest.json
files/
```

Directories use `0700`; manifest and candidate files use `0600`. The manifest
uses the scaffold candidate-manifest contract and records:

- candidate-set, owner, unit, and profile identity;
- the canonical normalized render inputs and their SHA-256;
- repository path, candidate-relative path, mode, and SHA-256 for every file;
- one passed offline candidate validation;
- dependency-backed post-apply validation requirements.

The manifest bytes and every candidate file are deterministic for the same
normalized request, independent of the private output location. Validation
recomputes the input digest, exact expected path set, validation records, and
private `0700`/`0600` permissions before accepting the handoff.

## Brownfield Rules

- Do not replace an existing `package.json`, TypeScript config, Vite config, or
  source file.
- Propose additive, non-conflicting package/config keys only after parsing and
  preserving every existing key.
- Existing source code is not an automatic semantic-merge target.
- Never hand-edit a lockfile. Generate one only through a separately authorized
  package-manager install and review its complete diff.

## Validation

Always:

```text
render -> validate candidate manifest -> scaffold finalize -> scaffold validate
```

The offline validator checks the closed request, exact assigned set, Unicode
and path safety, placeholders, JSON, manifest/file bindings, permissions, and
digests.

With dependencies already available or separately authorized, run only the
scripts present in the selected profile, followed by typecheck, test, and
build. Report every unrun dependency-backed check as post-apply pending.
