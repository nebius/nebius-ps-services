# Agentic SDLC Hook Bundle

This directory is the source bundle for the optional Agentic SDLC runtime
hooks. Keep hook fixes here first, then deliberately sync the bundle into a
local Codex home.

## Files

- `pre_tool_use_sdlc_policy.py`: PreToolUse guardrail for active SDLC runs.
- `stop_sdlc_continue.py`: Stop hook continuation guard for active SDLC runs.
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
- The PreToolUse hook should keep out-of-scope writes blocked. Do not weaken
  it to allow arbitrary edits to `$CODEX_HOME/hooks` from unrelated project
  workspaces.
- The PreToolUse hook may allow `apply_patch` edits to the exact resolved
  `$CODEX_HOME/AGENTS.md` file so reviewed global Codex guidance can be
  aligned. Do not allow that file to be deleted or moved, and do not extend
  the exception to shell writes, MCP writes, `$CODEX_HOME/config.toml`, or
  `$CODEX_HOME/hooks`. Shell commands that reference global `AGENTS.md` must
  be simple read-only inspections.
- The PreToolUse hook must allow writes under `$CODEX_HOME/task-state` so the
  global-context-management task-state file can be created and updated by the
  parent agent. This global continuity state is separate from private SDLC run
  state and from protected runtime hook files.

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
./install-skills.sh --install-hooks sdlc-start/assets/hooks
```

Set `CODEX_HOME` first to target a non-default Codex home. The all-hooks form
syncs every reviewed hook-only bundle under the source skills folder. The
single-source form copies only the SDLC hook scripts and tests into
`${CODEX_HOME:-$HOME/.codex}/hooks`. Neither form modifies `hooks.json`, trusts
hooks, or installs skills. After syncing, restart Codex and review the
PreToolUse and Stop hook entries in `/hooks`.
