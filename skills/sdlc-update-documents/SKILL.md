---
name: sdlc-update-documents
description: "Use only as part of the Agentic SDLC workflow; use after feature evaluation, resolved steering, UAT, or final run evidence to update project-facing README, changelog, usage docs, examples, or generated documentation without editing SDLC requirements or design."
---

# SDLC Update Documents

## Purpose

Update project-facing documentation from implemented behavior and SDLC evidence
after feature evaluation or UAT.

## When To Use

- A feature passed validation, tests, and evaluation and user-facing docs may
  need to match the implemented behavior.
- `sdlc-auto-steering` classified an entry as `docs-update`.
- UAT or final run review found README, changelog, examples, or usage docs that
  must be refreshed before final source integration or PR creation.
- `sdlc-start` routes a documentation phase before `sdlc-align-specs`.

## When Not To Use

- Do not use to write `docs/requirements.md`; route to
  `sdlc-create-requirements`.
- Do not use to write `docs/design.md`; route to `sdlc-create-design`.
- Do not use before implementation evidence exists for the behavior being
  documented.
- Do not use for generic documentation cleanup unrelated to the active SDLC
  feature or run.

## Inputs

- Active run state under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- `docs/requirements.md` and `docs/design.md`.
- Current feature locked plan, implementation diff, validation evidence, test
  evidence, evaluation evidence, and resolved steering entries.
- UAT evidence for run-level documentation updates.
- Existing README, changelog, usage docs, examples, generated docs, and docs
  index files in the target project when present.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- `STEERING.md` and `steering/auto-steering.json` when present.
- `current-state.json`, latest checkpoint, feature queue, and fingerprints.
- Current feature plan and evidence under `evidence/FEAT-*`.
- UAT evidence under `evidence/uat/` for run-level documentation updates.
- Existing target documentation files that mention the changed behavior.
- `assets/templates/documents.md.template` when writing documentation evidence.

## Writes

- Project-facing documentation files that are in scope for the active feature,
  such as `README.md`, user docs, examples, docs indexes, or generated docs.
- The `[Unreleased]` section of `CHANGELOG.md` when the project has one and the
  behavior change is user-facing.
- `evidence/FEAT-*/documents.md` for feature-level documentation updates.
- `evidence/uat/documents.md` for run-level or UAT-driven documentation
  updates.
- Steering disposition updates marking document entries resolved, superseded,
  or blocked.

## Process

- Resolve and verify the registered integration worktree before editing project
  documentation. Keep the original project checkout unchanged until final
  promotion, and record the resulting integration HEAD in document evidence.
- Reload active run state and evidence before editing any documentation.
- Determine whether the scope is `feature` or `run`: feature scope runs after
  evaluation; run scope runs after UAT or before final handoff.
- Compare implemented behavior and evidence against existing docs. Update only
  documentation that describes the active feature, run-level product behavior,
  commands, configuration, examples, or usage that actually changed.
- When docs describe a multi-layer feature flow, base the wording on evaluated
  end-to-end slice evidence instead of implementation intent alone. If the
  planned slice was not evaluated, route the gap back to `sdlc-evaluate`.
- Record documentation evidence with files changed, source evidence, steering
  entries consumed, and any docs intentionally left unchanged.
- Mark consumed `docs-update` steering entries resolved only after the relevant
  documentation has been updated or a clear no-op reason is recorded.
- Route requirements or design drift back to `sdlc-create-requirements` or
  `sdlc-create-design` instead of editing product-truth docs here.
- Return to `sdlc-start` so the coordinator can continue to
  `sdlc-align-specs`, UAT, managed source integration, or unmanaged PR handoff.

## Idempotency

- Rerunning with unchanged implementation evidence and documentation must
  produce a no-op evidence update, not duplicate prose or changelog bullets.
- Preserve existing changelog grouping and add only one clear `[Unreleased]`
  entry for the changed behavior.
- Do not rewrite broad documentation for style when a narrow update is enough.
- If documentation is already accurate, record the no-op with evidence and
  leave files unchanged.

## Failure Handling

- Missing implementation, validation, test, or evaluation evidence routes back
  to the responsible phase.
- Missing evaluated slice evidence for docs that describe multi-layer behavior
  routes back to `sdlc-evaluate`.
- Requirements drift routes to `sdlc-create-requirements`.
- Design drift routes to `sdlc-create-design`.
- Ambiguous user-facing wording or unresolved product decision maps to
  `HUMAN_INPUT_REQUIRED`.
- Documentation conflicts that cannot be resolved from evidence map to
  `DOCUMENTATION_DRIFT`.

## Must Not

- Edit `docs/requirements.md` or `docs/design.md`.
- Document behavior that has not been implemented and evaluated.
- Hide failed validation, tests, evaluation, or UAT by updating docs as if the
  feature passed.
- Commit, push, create PRs, review PRs, or merge.
- Store secrets, private endpoints, customer data, raw logs, or local-only run
  paths in project documentation.
- Absorb unrelated documentation cleanup into the active SDLC feature.

## Completion Criteria

- In-scope user-facing docs match implemented and evaluated behavior.
- Multi-layer behavior docs are backed by evaluated end-to-end slice evidence
  when applicable.
- Changelog is updated when a user-facing behavior, command, workflow, or
  documentation contract changed and a changelog exists.
- Documentation evidence is written under the active private run directory.
- Consumed steering entries are resolved or left with a clear blocker.
- `sdlc-start` can route to `sdlc-align-specs`, UAT, or PR handoff.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only
  `sdlc-create-design` writes `docs/design.md`. Other skills route spec
  changes to those owners.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under
  `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different
  SDLC shape.
- Classify every failure before retrying or routing backward.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the
  workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Documentation files updated or confirmed no-op.
- Changelog update status.
- Documentation evidence path.
- Steering entries resolved or blocked.
- Failure classification and next recommended skill when blocked.
- Confirmation that requirements and design product-truth docs were not edited.

## References

- Use `assets/templates/documents.md.template` when creating documentation
  evidence.
