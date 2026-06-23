# Start SDLC

`sdlc-start` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Coordinate the SDLC loop by reading specs, checkpoints, and local state,
selecting the next feature, and choosing exactly one next skill.

## Main Boundaries

- Do not free-edit requirements or design.
- Do not implement code directly.
- Do not commit, push, create PRs, review PRs, or merge.
- Do not bypass validation, tests, or evaluation.

## Primary Inputs

- `docs/requirements.md`.
- `docs/design.md` when present.
- Existing local run state when present.
- User instruction or continuation prompt.

## Output

- Active run state is accurate and backed by a checkpoint.
- Current feature and next skill are explicit.
- Each state transition writes a checkpoint and history entry.
- Repeated resumes without state changes do not duplicate history.
- The loop can resume after context loss.

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
`$CODEX_HOME/hooks`. The Stop hook must emit `sdlc-start` and canonical
`sdlc-*` skill names, while still accepting short phase aliases as input. The
PreToolUse hook does not block file targets by path; repo files, outside-repo
files, credential directories, Codex runtime files, global `AGENTS.md`, locked
SDLC plans, and private SDLC state are all path-allowed for operator
flexibility. It still blocks secret-bearing payloads, dangerous shell patterns,
and guarded Git/GitHub actions.

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
should merge the SDLC `PreToolUse` and `Stop` entries into `hooks.json`.
Registration does not trust hooks. Restart Codex and review the PreToolUse and
Stop entries in `/hooks` after syncing.
