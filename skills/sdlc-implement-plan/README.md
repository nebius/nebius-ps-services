# Implement Plan

`sdlc-implement-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Coordinate dependency waves for one feature. Every safe task uses one fresh
agent with its own branch and private worktree. The coordinator verifies one
task commit, merges workers in stable order, runs combined evidence, and
non-force-cleans worker resources before advancing.

Native isolated agents are preferred. When unavailable, the private launcher
uses one sequential ephemeral `codex exec` process per assignment with exact
scope cwd, `workspace-write`, stdin instructions, and schema-bound output. The
coordinator—not the worker—performs the sensitive-content gate and task commit.

## Main Boundaries

- Do not change unrelated features.
- Do not modify locked plans.
- Do not hide failures.
- Do not remove tests to pass.
- Do not broaden implementation beyond the locked vertical slice; route plan or
  design defects backward instead.
- Do not let workers share agents, branches, worktrees, write claims, or
  conflict domains.
- Do not rewrite history or force-clean resources.

## Primary Inputs

- Locked plan.
- Red or ready tests.
- Feature design.
- Context pack.
- Existing codebase.
- Prepared execution coordinator and task-wave assignments.

## Output

- Planned task waves are implemented and integrated.
- Planned vertical slice is implemented or a plan/design defect is recorded.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- Every task has validation, review, one direct-child commit, and cleanup proof.
- State moves to `implemented`.
