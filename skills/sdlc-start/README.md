# Start SDLC

`sdlc-start` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Expose a durable private prompt workspace and coordinate the SDLC loop by
reading accepted prompt revisions, specs, checkpoints, and local state,
encouraging a safe live experiment environment when useful, selecting the next
feature, refreshing active steering when needed, and choosing exactly one next
skill. After plan lock it routes through execution preparation, keeps one active
feature while safe implementation tasks run in dependency waves, and returns
to the project checkout only after exact promotion. In a managed outer
worktree, it releases Agentic SDLC ownership only after final alignment, UAT,
documentation evidence, exact promoted-HEAD validation, and internal-resource
cleanup; PR publication begins afterward.

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

## Main Boundaries

- Do not free-edit requirements or design.
- Do not implement code directly.
- Do not commit, push, create PRs, review PRs, or merge.
- Do not bypass validation, tests, or evaluation.
- Do not store live environment credentials or raw logs in run state.
- Do not let hooks, cron, or background daemons select SDLC phases.

## Primary Inputs

- `docs/requirements.md`.
- `docs/design.md` when present.
- Existing local run state when present.
- One managed `agentic-sdlc/prompt-v1` file and its accepted immutable revision.
- Optional live experiment environment details to route through requirements.
- Active `STEERING.md` and `steering/auto-steering.json` when present.

## Output

- Active run state is accurate and backed by a checkpoint.
- Current feature and next skill are explicit.
- Steering is refreshed or routed through `sdlc-auto-steering` when pending.
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
filename mirror disagrees. The PreToolUse hook does not block file targets
by path; repo files, outside-repo files, credential directories, Codex runtime
files, global `AGENTS.md`, locked SDLC plans, and private SDLC state are all
path-allowed for operator flexibility. It still blocks secret-bearing payloads,
dangerous shell patterns, and guarded Git/GitHub actions.
Coordinator-registered integration and worker worktrees outside the original
checkout remain inside active-run policy; sensitive raw Git actions require
exact identity and action-scoped authorization.

Validate the source bundle from the `skills/` directory:

```bash
python3 sdlc-start/assets/hooks/tests/test_sdlc_hooks.py
```

To intentionally sync the source bundle into a local Codex runtime:

```bash
./install-skills.sh --install-all-hooks
./install-skills.sh --install-hooks sdlc-start/assets/hooks --register-hooks
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
