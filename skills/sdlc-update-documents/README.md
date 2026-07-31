# SDLC Update Documents

`sdlc-update-documents` is an Agentic SDLC skill. It is authored in this
repository and is installed into a Codex runtime only when `install-skills.sh`
is run.

## What It Does

Updates project-facing documentation after implementation evidence, feature
evaluation, resolved steering, UAT, or final run review shows docs need to
match implemented behavior. Multi-layer behavior docs must be backed by
evaluated end-to-end slice evidence when applicable.

## Main Boundaries

- Do not edit `docs/requirements.md` or `docs/design.md`.
- Do not document behavior before it is implemented and evaluated.
- Do not commit, push, create PRs, review PRs, or merge.
- Do not include secrets, private endpoints, customer data, raw logs, or
  local-only run paths in project docs.

## Primary Inputs

- Active run state.
- Requirements, design, locked plan, implementation diff, and evidence.
- Resolved `docs-update` steering entries.
- README, changelog, usage docs, examples, and generated docs in scope.

## Output

- In-scope project-facing docs match implemented behavior.
- Multi-layer behavior docs cite evaluated slice evidence or remain blocked.
- Changelog is updated when user-facing behavior changed.
- Documentation evidence is recorded in private SDLC run state.
