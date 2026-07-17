---
name: agentic-sdlc-test
description: "Use only when explicitly asked, outside the Agentic SDLC workflow, to safely verify the whole Agentic SDLC system against docs/agentic-sdlc-design.md: check source-installed skill parity, deterministic prompt/execution/worktree/hook capabilities, optional private live-run evidence, and readiness reporting without changing real projects, installed skills, hooks, or agent configuration."
---

# Agentic SDLC Test

## Purpose

Verify whether the Agentic SDLC workflow works as designed without joining the
workflow as an SDLC phase. This skill is a test harness and review workflow for
the SDLC system itself.

## When To Use

- The user asks to test, verify, validate, audit, or smoke-check the Agentic
  SDLC workflow.
- The user wants to know whether global `sdlc-*` skills and optional SDLC hooks
  are installed and working as designed.
- The user wants a safe verification report before trying the SDLC workflow on
  a real repository.
- The SDLC design, hook source, state schema, or phase skills changed and need
  a disposable regression pass.

## When Not To Use

- Do not use as part of a normal Agentic SDLC product run.
- Do not use to implement product features directly.
- Do not use to install, sync, trust, or repair hooks unless the user asks for
  that separate remediation after reviewing the report.
- Do not use on production repositories or real customer projects.
- Do not use as a replacement for `sdlc-start` or any `sdlc-*` phase skill.

## Inputs

- `docs/agentic-sdlc-design.md`.
- Optional user-specified dedicated verification root, report path under that
  private verification root, global skills path, or design path. A new custom
  root is initialized with an ownership marker; an existing custom root must
  already contain that valid marker.
- Existing global `sdlc-*` skills under `~/.agents/skills`.
- Existing Codex hook configuration under `~/.codex/hooks.json` or
  `~/.codex/config.toml`.
- Optional prior verification report and disposable state under
  `~/.codex/sdlc-verification/`.
- Optional private live-results manifest passed with `--live-evidence PATH`.
  The default is `~/.codex/sdlc-verification/live-results.json`; its contract
  is `assets/live-results.schema.json`.

The default report path is:

```text
~/.codex/sdlc-verification/report.md
```

## Must Not

- Do not create a new SDLC CLI or make hooks orchestrate phases.
- Do not create project-local skills.
- Do not modify installed global skills under `~/.agents/skills`.
- Do not edit, delete, install, trust, or rewrite hooks under `~/.codex/hooks`
  or hook configuration.
- Do not run on a production or user project. Use only the disposable
  verification project.
- Do not push, create a real PR, merge, force-push, or publish anything.
- Do not commit private SDLC state, hook logs, screenshots, transcripts, local
  plans, or evidence.
- Do not treat conversation memory as authoritative workflow state.

## Required Reads

- `docs/agentic-sdlc-design.md`.
- `references/verification-checklist.md`.
- `assets/live-results.schema.json` when producing or ingesting live evidence.
- The `SKILL.md` files for all `sdlc-*` skills being verified.
- The SDLC hook README, PreToolUse hook, Stop hook, and hook unit tests from
  the `sdlc-start` skill's hook bundle when hook verification is in scope.
- `scripts/verify_agentic_sdlc.py` before patching or relying on verifier
  behavior beyond its command-line help.

## Writes

Allowed writes:

- `~/.codex/sdlc-verification/`.
- A disposable verification project under
  `~/.codex/sdlc-verification/disposable-project/`.
- Disposable local state for the verification project only.
- `~/.codex/sdlc-verification/report.md`.
- An explicitly selected report path only when it remains under the private
  verification root and has no symlinked component.
- `~/.codex/sdlc-verification/verification-context.json` and optional
  `live-results.json` plus referenced evidence artifacts, all private local
  files outside the disposable Git root.
- A private verification-root ownership marker and a committed public fixture
  marker that prevent custom roots or clean unknown Git repositories from
  being mistaken for verifier-owned state.

Do not write to real project source trees, installed skill folders, hook
configuration, credential directories, external systems, or non-disposable Git
remotes.

## Process

1. Establish the source of truth.
   Read `docs/agentic-sdlc-design.md` and
   `references/verification-checklist.md`. Treat the design doc as the
   workflow contract and the checklist as the test plan.
2. Run static and hook preflight verification.
   From the skills repository root, run:

   ```bash
   python3 agentic-sdlc-test/scripts/verify_agentic_sdlc.py
   ```

   This script checks source-installed parity for all required SDLC skills and
   `worktree`, explicit-only invocation policy, prompt workspace/history/
   rename/lifecycle regressions, execution scope/recovery/replan/secret gates,
   sequential fallback, Task Implementer interoperability, the composed
   managed outer-worktree lease lifecycle, verifier self-tests, and hook
   fixtures. The disposable fixture is a nested selected folder in a local
   monorepo-shaped Git repository. A clean canonical flat fixture with the
   exact expected tracked tree and no remote is migrated once. Unknown,
   unowned, dirty, remote-backed, or non-canonical directories and repositories
   fail closed without mutation. The script does not edit installed skills or
   hooks.
3. Review the preflight report.
   Any deterministic FAIL makes the report FAIL. Missing optional hook
   registration is WARN/PARTIAL; a configured payload mismatch or unsafe hook
   behavior is FAIL. Missing live evidence is PARTIAL, not synthetic PASS.
4. Run the disposable golden-path workflow when full verification is requested.
   Use the disposable project only. Explicitly load and follow these phase
   skills in order:
   `sdlc-create-requirements`, `sdlc-start`, `sdlc-gather-context`,
   `sdlc-create-design`, `sdlc-auto-steering`, `sdlc-create-plan`,
   `sdlc-prepare-execution`, `sdlc-tdd`,
   `sdlc-implement-plan`, `sdlc-validate-codes`, `sdlc-unit-tests`,
   `sdlc-evaluate`, `sdlc-update-documents`, `sdlc-align-specs`,
   `sdlc-commit`, and `sdlc-uat-tests`. Run `sdlc-update-documents` again
   after UAT when final docs changed. Do not use `sdlc-merge-pr`, and do not
   create a real PR.
5. Verify rerun and change-request behavior.
   Repeat `$sdlc-start run <prompt-path-or-unique-filename>` with no prompt
   changes, then edit the same prompt with the safe change
   request from the checklist and confirm stable IDs, immutable locked plans,
   scoped changes, refreshed evidence, and no duplicate commits.
6. Verify failure routing and steering behavior.
   Inject one controlled failure at a time in the disposable project, verify
   `sdlc-classify-failure` routes to the earliest responsible phase, then
   repair and rerun. Add the pause/no-PR instruction to the same prompt and
   repeat `run`.
7. Verify continuation and optional harness smoke checks.
   Exercise Stop continuation with fake state and, where available, run safe
   GUI and TUI smoke checks against local disposable targets only.
8. Persist and ingest live results.
   Use the identity in `verification-context.json`. Write only the v1 manifest
   and relative evidence paths defined by `assets/live-results.schema.json`.
   Evidence must stay under `evidence/<lane>/` within the verification root,
   use private permissions, match the exact preserved baseline/final Git
   identity, include a real selected-scope golden-path commit, keep every commit
   in the live history inside the selected nested project, exclude private SDLC
   state, and never contain prompt bodies or secrets. Rerun the verifier with
   `--live-evidence PATH`.
9. Update the report.
   Keep the report concise and evidence-backed. Include capability-level PASS,
   PARTIAL, or FAIL, validation commands, skipped live checks, and the low-risk
   repository recommendation. Do not paste raw evidence bodies, hook logs, or
   secret-bearing output.

## Idempotency

- The verifier may be rerun at any time.
- Reuse the same verification root and overwrite only generated verification
  files under `~/.codex/sdlc-verification/`.
- Preserve or supersede prior reports by writing the current report atomically.
- Do not duplicate requirements, design, plans, tests, commits, or evidence in
  the disposable project when inputs are unchanged.
- If a previous verification run is incomplete, resume from the report and
  disposable state instead of deleting unrelated user files.

## Failure Handling

- If required global skills, `worktree`, or installed parity are missing, write
  FAIL. Missing optional hook registration is PARTIAL; configured source/
  installed hook drift is FAIL.
- If hook fixture tests fail, keep all fixture state under the verification
  root and report the failing checks without mutating installed hooks.
- If a deterministic subprocess times out or cannot start, record a concise
  FAIL result and still write the report.
- If execution-plane scheduler or real-Git lifecycle tests fail, stop before
  the golden path; do not attempt worker dispatch or promotion.
- If the disposable project is dirty from a prior verification run, inspect and
  reuse or supersede only verification-owned files; do not delete unknown files.
- If the verification root, disposable project, canonical fixture paths,
  installed skill roots, configured hook payloads, or requested report path
  contain symlinks, fail closed before following or mutating them.
- If an existing custom verification root lacks its ownership marker, or an
  existing disposable directory is non-empty and not an exact marked verifier
  Git fixture or canonical flat migration source, fail closed without chmod,
  file writes, or commits. Any disposable Git remote is also a failure.
- If hook configuration is malformed or an SDLC hook command does not target
  the canonical payload under `$CODEX_HOME/hooks`, report FAIL rather than
  treating registration as missing or comparing an unrelated canonical file.
- If the golden-path SDLC run fails, route through `sdlc-classify-failure` and
  record the earliest responsible phase in the report.
- If live evidence is absent, stale, dirty, symlinked, overly permissive,
  outside the verification root, or schema-invalid, never infer success.
- If a check requires unavailable optional tooling such as a GUI harness, mark
  it NOT APPLICABLE or PARTIAL instead of failing the core workflow.
- If any command would push, publish, merge, edit installed hooks, or touch a
  non-disposable project, stop and report the unsafe action.

## Safety

The helper script is limited to static inspection, local fixture setup, local
Git operations inside the disposable verification project, and hook execution
with a disposable `CODEX_HOME`. Full workflow verification may create local
commits only inside the disposable project. It must never push, open real PRs,
merge, publish, alter credentials, or mutate installed hooks or skills.

The verification root must be a dedicated, verifier-owned directory outside
the source repository. It uses private `0700` permissions on POSIX; the root
marker, context, manifest, report, and referenced evidence files require
private modes. The disposable Git root must have no remote and must carry the
exact public fixture marker. The root, fixture components, and report path must
be real local directories and files, never symlink redirects.

When executing hook source fixtures, disable Python bytecode writes so
verification does not create `__pycache__` artifacts in skill source folders.

If the active environment appears to be a real production repository, stop and
ask the user to confirm a disposable verification path.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Completion Criteria

- `~/.codex/sdlc-verification/report.md` exists.
- Static skill discovery and hook configuration checks are recorded.
- PreToolUse and Stop hook fixture results are recorded.
- Prompt workspace initialization, revision, steering, terminal, and legacy
  lifecycle results are recorded.
- The deterministic capability matrix records prompt, execution, fallback,
  outer lease, Task Implementer interop, steering, hook, and verifier results.
- Full verification records the golden-path, idempotency, change-request,
  failure-routing, auto-steering, documentation-update, and
  steering-continuation lanes through validated private evidence; missing lanes
  remain PARTIAL.
- Any required deterministic or supplied live FAIL makes final status FAIL;
  PASS requires all required deterministic and live lanes to pass.
- The report states PASS, PARTIAL, or FAIL and lists top issues and fixes.
- No installed skills, hook configuration, credentials, real repositories, or
  external services were modified.

## Output Contract

Return:

- Final readiness status: PASS, PARTIAL, or FAIL.
- Report path.
- Top issues found.
- Recommended fixes.
- Whether it is safe to try the SDLC workflow on a low-risk real repository.
- Validation commands run.
- Live or external tests skipped and why.
