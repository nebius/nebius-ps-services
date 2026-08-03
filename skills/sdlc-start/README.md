# Start SDLC

`sdlc-start` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Expose a durable private prompt workspace and coordinate the SDLC loop by
reading accepted prompt revisions, specs, checkpoints, and local state,
encouraging a safe live experiment environment when useful, selecting the next
feature, conditionally establishing project-specific agent instructions after
requirements and design, refreshing active steering when needed, and choosing
exactly one next skill. After plan lock it routes through execution
preparation, keeps one active
feature while safe implementation tasks run in dependency waves, and returns
to the project checkout only after exact promotion. In a managed outer
worktree, it releases Agentic SDLC ownership only after final alignment, UAT,
documentation evidence, exact promoted-HEAD validation, and internal-resource
cleanup; PR publication begins afterward in exact-SHA publication-only mode,
followed by findings-and-readiness-only review. Any required branch change
returns through failure classification and the coordinator.

Gate failures use one private, deterministic repair-control contract.
Already-proven mechanical causes take the shortest route to their owning
phase. Ambiguous or persistent failures enter `troubleshoot` as a conditional
diagnostic branch and return to classification before any repair dispatch.
Blocker and feature budgets, invalidations, corrective-plan re-entry, and stop
outcomes remain checkpointed; unresolved evidence never becomes speculative
redesign.

## Public Interface

```text
$sdlc-start workspace init [project-folder]
$sdlc-start run <prompt-path-or-unique-filename>
```

Initialization preserves prompt and run history and creates one starter prompt
only when the workspace is empty. Its editor workspace includes private new
prompt and metadata-only history tasks. Edit the same prompt and repeat `run` to
steer an active run. Unchanged runs resume idempotently; an unchanged completed
prompt returns `ALREADY_COMPLETE`; editing a completed prompt starts a new run.
An exact manual rename is repaired, while rename-plus-edit or duplicate copies
fail closed so prompt history and Stop continuation cannot drift.
There is no bare `$sdlc-start` resume action.

For a Git-backed workspace, intake resolves the actual symbolic `origin`
default. A new run on that clean branch is moved automatically to a
deterministic `feature/sdlc-<run-hash>` promotion branch. Resumes reuse the
recorded branch, including preserving unfinished dirty changes; they never
silently create a replacement worktree.

## Main Boundaries

- Do not free-edit requirements or design.
- Do not create, overwrite, or delete project instruction files directly.
- Do not implement code directly.
- Do not commit, push, create PRs, review PRs, or merge.
- Do not bypass validation, tests, or evaluation.
- Do not store live environment credentials or raw logs in run state.
- Do not let hooks, cron, or background daemons select SDLC phases.
- Do not reopen completed execution or route a troubleshooting diagnosis
  directly to implementation/design.

## Primary Inputs

- `docs/requirements.md`.
- `docs/design.md` when present.
- The active selected-project instruction file and latest private
  `project-agent-instructions` decision when present.
- Existing local run state when present.
- One managed `agentic-sdlc/prompt-v1` file and its accepted immutable revision.
- Optional live experiment environment details to route through requirements.
- Active `STEERING.md` and `steering/auto-steering.json` when present.

## Output

- Active run state is accurate and backed by a checkpoint.
- Current feature and next skill are explicit.
- Steering is refreshed or routed through `sdlc-auto-steering` when pending.
- The conditional project-instruction decision is verified after requirements
  and design and before auto-steering or planning.
- Each state transition writes a checkpoint and history entry.
- Repeated resumes without state changes do not duplicate history.
- The loop can resume after context loss from the same prompt-bound command.

## Optional Hook Bundle

`sdlc-start/assets/hooks/` is the source bundle for optional Agentic SDLC
runtime hooks:

- `pre_tool_use_sdlc_policy.py`
- `stop_sdlc_continue.py`
- `lib/__init__.py`
- `lib/sdlc_policy.py`
- `lib/sdlc_state.py`
- `tests/test_sdlc_hooks.py`

Patch these source files before touching installed runtime copies under
`$CODEX_HOME/hooks`. The Stop hook must emit prompt-bound continuation prompts
for `sdlc-start run` and canonical `sdlc-*` skill
names, while still accepting short phase aliases as input. It validates the
managed `prompt.json` binding and fails closed if an optional `run.json` prompt
filename mirror disagrees. When current state includes a repair pointer, the
Stop hook also validates that the event, stable blocker, control projection,
optional diagnosis, budget status, and next route agree. Diagnosis-required
work may continue only to `troubleshoot`; diagnosed work may continue only
back to `sdlc-classify-failure`. The PreToolUse hook does not block file targets
by path; repo files, outside-repo files, credential directories, Codex runtime
files, global `AGENTS.md`, locked SDLC plans, and private SDLC state are all
path-allowed for operator flexibility. A global registration is selected by
tool name before it can inspect SDLC state, so the hook immediately allows a
call when no active SDLC run covers the working directory and omits a static
SDLC status message. During an active run it still blocks secret-bearing
payloads, dangerous Bash command patterns, and guarded Git/GitHub actions.
Dangerous shell matching is not applied to patch contents, so Dockerfile and
documentation command examples remain subject to patch secret/target/spec
checks instead of being treated as commands being executed. Public Nebius
profile, project, and credential-file path assignments are allowed as metadata;
all other long Nebius assignments remain fail-closed during an active run.
Coordinator-registered integration and worker worktrees outside the original
checkout remain inside active-run policy; sensitive raw Git actions require
exact identity and action-scoped authorization.
PR authorization and creation also require an explicit base equal to the
resolved `origin` default; the hook does not guess a default branch name.

Validate the source bundle from the `skills/` directory:

```bash
python3 sdlc-start/assets/hooks/tests/test_sdlc_hooks.py
```

To intentionally sync the source bundle into a local Codex runtime:

```bash
./install-skills.sh --install-all-hooks --register-hooks --refresh-hook-registrations
./install-skills.sh --install-hooks sdlc-start/assets/hooks --register-hooks --refresh-hook-registrations
```

The all-hooks form syncs every reviewed hook-only bundle under the source skills
folder. The single-source form copies only SDLC hook files into
`${CODEX_HOME:-$HOME/.codex}/hooks`. Add `--register-hooks` when the installer
should merge the SDLC `PreToolUse` and `Stop` entries into `hooks.json`. Add
`--replace-hooks-json` only when intentionally replacing `hooks.json` with a
clean file built from the selected source manifests. Hook payload sync records
local provenance hashes; differing existing hook files are backed up under
`${CODEX_HOME:-$HOME/.codex}/.install-hooks-state/backups/`, then refreshed
from source.
Registration is preflighted before payload sync, idempotent, does not trust
hooks, refuses duplicate Python hook files within the same hook event, and
reports extra installed hook files or `hooks.json` entries for manual review
instead of removing them by default.
Restart Codex and review the PreToolUse and Stop entries in `/hooks` after
syncing.
