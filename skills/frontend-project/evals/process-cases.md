# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Static examples do not prove runtime activation.

## Quality Checks

- Uses only React, TypeScript, and Vite.
- Resolves exact compatible versions from approved input or official docs.
- Never runs a native generator or installs dependencies by default.
- Separates standalone and coordinated-candidate ownership.
- In coordinated mode, emits deterministic candidates only for the exact
  assigned frontend root and returns profile/input/file digests plus bound
  validations.
- Generates only public `VITE_*` names with empty example values and rejects
  secret-like names, separator-normalized API/access-key markers, or supplied
  values.
- Accepts only npm, pnpm, Yarn, or Bun as the approved package manager.
- Adds routing, lint, and format files only when their approved profiles and
  exact versions are supplied.
- Never claims root CI, ignore, container, infrastructure, Helm, or agent files.
- Reports lockfile, dependency-backed tests, and builds as pending when unrun.

## Manual Runtime Check

In a fresh Codex thread where the source skill is installed or discoverable,
run canonical rows `frontend-positive-01` through `frontend-positive-05` and
`frontend-negative-01` through `frontend-negative-06`.

- Positive rows should preserve the standalone/coordinated ownership split,
  deterministic candidate contract, public environment-name checks, and
  no-install default.
- Negative rows should stay with stack selection, backend, container, Sites,
  other-framework, or repository-root workflow owners.
- Static examples do not prove runtime activation; report it as observed only
  after this check.
