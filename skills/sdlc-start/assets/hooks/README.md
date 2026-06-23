# Agentic SDLC Hook Bundle

This directory is the source bundle for the optional Agentic SDLC runtime
hooks. Keep hook fixes here first, then deliberately sync the bundle into a
local Codex home.

## Files

- `pre_tool_use_sdlc_policy.py`: PreToolUse guardrail for active SDLC runs.
- `stop_sdlc_continue.py`: Stop hook continuation guard for active SDLC runs.
- `lib/__init__.py`: package marker for static import resolution.
- `lib/sdlc_policy.py`: shared policy helpers.
- `lib/sdlc_state.py`: shared state and Git helpers.
- `tests/test_sdlc_hooks.py`: local unit tests for the hook bundle.

## Maintenance Contract

- Hooks are runtime guardrails, not skills. They must not make workflow phase
  decisions that belong to `sdlc-start`.
- The Stop hook must route continuation through `sdlc-start` and use canonical
  `sdlc-*` skill names in prompts. Short phase aliases may be accepted as
  input, but they must not be emitted as the next recommended skill.
- `STEERING.md` pause and PR-control text, including `Pause after the current
  feature. Do not create a PR.`, must trigger a continuation through
  `sdlc-start` so the coordinator can persist the paused or no-PR decision.
- The PreToolUse hook does not block file targets by path. Repo files,
  outside-repo files, credential directories, Codex runtime files, global
  `AGENTS.md`, locked SDLC plans, `$CODEX_HOME/task-state`, and private SDLC
  state are all path-allowed for operator flexibility.
- The PreToolUse hook must still block secret-bearing payloads, dangerous shell
  patterns, and guarded Git/GitHub actions. Ordinary outbound network commands
  are not restricted by the hook unless they match one of those unsafe checks.

## Validate

From the `skills/` directory:

```bash
python3 sdlc-start/assets/hooks/tests/test_sdlc_hooks.py
python3 - <<'PY'
from pathlib import Path

for path in (
    Path("sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py"),
    Path("sdlc-start/assets/hooks/stop_sdlc_continue.py"),
    Path("sdlc-start/assets/hooks/lib/sdlc_policy.py"),
    Path("sdlc-start/assets/hooks/lib/sdlc_state.py"),
):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("hook scripts parse")
PY
```

## Install To Local Runtime

Use the explicit installer option so hook updates are not hidden inside the
normal skill installation path:

```bash
./install-skills.sh --install-all-hooks
./install-skills.sh --install-hooks sdlc-start/assets/hooks --register-hooks
```

Set `CODEX_HOME` first to target a non-default Codex home. The all-hooks form
syncs every reviewed hook-only bundle under the source skills folder. The
single-source form copies only the SDLC hook scripts and tests into
`${CODEX_HOME:-$HOME/.codex}/hooks`. Add `--register-hooks` when the installer
should merge the SDLC `PreToolUse` and `Stop` entries into `hooks.json`. Add
`--replace-hooks-json` only when intentionally replacing `hooks.json` with a
clean file built from the selected source manifests. Registration is
idempotent, does not trust hooks or install skills, refuses duplicate Python
hook files within the same hook event, and reports extra installed hook files
or `hooks.json` entries for manual review instead of removing them by default.
After syncing, restart Codex and review the PreToolUse and Stop hook entries in
`/hooks`.
