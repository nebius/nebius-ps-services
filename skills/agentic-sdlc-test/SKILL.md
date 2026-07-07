---
name: agentic-sdlc-test
description: "Use outside the Agentic SDLC workflow to safely verify the whole Agentic SDLC system against docs/agentic-sdlc-design.md: discover global sdlc-* skills, inspect hook configuration, run disposable PreToolUse and Stop hook fixture tests, orchestrate a disposable golden-path SDLC run, check idempotency/failure/steering behavior, and write ~/.codex/sdlc-verification/report.md without changing real projects, installed skills, hooks, or agent configuration."
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
- Optional user-specified verification root, report path, global skills path,
  or design path.
- Existing global `sdlc-*` skills under `~/.agents/skills`.
- Existing Codex hook configuration under `~/.codex/hooks.json` or
  `~/.codex/config.toml`.
- Optional prior verification report and disposable state under
  `~/.codex/sdlc-verification/`.

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

   This script performs read-only discovery of global `sdlc-*` skills,
   explicit-only `agents/openai.yaml` invocation policy, and hook
   configuration, creates a disposable verification root, runs hook fixture
   tests against disposable `CODEX_HOME` state, and writes a report. It does
   not edit installed skills or hooks.
3. Review the preflight report.
   If skill discovery, hook configuration, PreToolUse safety, or Stop
   continuation checks fail, record the issue and stop before the golden path
   unless the failure is unrelated to workflow safety.
4. Run the disposable golden-path workflow when full verification is requested.
   Use the disposable project only. Explicitly load and follow these phase
   skills in order:
   `sdlc-create-requirements`, `sdlc-start`, `sdlc-gather-context`,
   `sdlc-create-design`, `sdlc-auto-steering`, `sdlc-create-plan`, `sdlc-tdd`,
   `sdlc-implement-plan`, `sdlc-validate-codes`, `sdlc-unit-tests`,
   `sdlc-evaluate`, `sdlc-update-documents`, `sdlc-align-specs`,
   `sdlc-commit`, and `sdlc-uat-tests`. Run `sdlc-update-documents` again
   after UAT when final docs changed. Do not use `sdlc-merge-pr`, and do not
   create a real PR.
5. Verify rerun and change-request behavior.
   Rerun `sdlc-start` with no product changes, then apply the safe change
   request from the checklist and confirm stable IDs, immutable locked plans,
   scoped changes, refreshed evidence, and no duplicate commits.
6. Verify failure routing and steering behavior.
   Inject one controlled failure at a time in the disposable project, verify
   `sdlc-classify-failure` routes to the earliest responsible phase, then
   repair and rerun. Test `STEERING.md` with the pause/no-PR instruction.
7. Verify continuation and optional harness smoke checks.
   Exercise Stop continuation with fake state and, where available, run safe
   GUI and TUI smoke checks against local disposable targets only.
8. Update the report.
   Keep the report concise and evidence-backed. Include PASS, PARTIAL, or FAIL
   and the top issues/fixes. Do not paste raw hook logs or secret-bearing
   output.

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

- If required global skills or hook configuration are missing, write a FAIL or
  PARTIAL report and stop before the golden-path run.
- If hook fixture tests fail, keep all fixture state under the verification
  root and report the failing checks without mutating installed hooks.
- If the disposable project is dirty from a prior verification run, inspect and
  reuse or supersede only verification-owned files; do not delete unknown files.
- If the golden-path SDLC run fails, route through `sdlc-classify-failure` and
  record the earliest responsible phase in the report.
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

When executing hook source fixtures, disable Python bytecode writes so
verification does not create `__pycache__` artifacts in skill source folders.

If the active environment appears to be a real production repository, stop and
ask the user to confirm a disposable verification path.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Completion Criteria

- `~/.codex/sdlc-verification/report.md` exists.
- Static skill discovery and hook configuration checks are recorded.
- PreToolUse and Stop hook fixture results are recorded.
- Full verification either records the disposable golden-path, idempotency,
  change-request, failure-loop, steering, and continuation results or clearly
  marks them as pending with a PARTIAL status.
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
