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
- The Stop hook must route continuation through the explicit prompt-bound
  `sdlc-start run` action and use canonical `sdlc-*` skill names in prompts. Short phase
  aliases may be accepted as input, but they must not be emitted as the next
  recommended skill.
- When `current-state.json` has an
  `agentic-sdlc/repair-state-pointer-v1`, the Stop hook validates that its
  `failure-event-v1`, optional `diagnosis-v1`, and `repair-control-v1` remain
  inside the run and agree on feature, event, and stable blocker identity. It
  stops on exhausted budgets or semantic-cycle status, requires
  diagnosis-required work to route to `troubleshoot`, and requires a recorded
  diagnosis to return through `sdlc-classify-failure`. After successful repair,
  it also enforces the authoritative revalidation cursor so commit, UAT, PR,
  and publication cannot bypass invalidated validation, test, evaluation,
  documentation, alignment, or commit evidence.
- `STEERING.md` pause and PR-control text, including `Pause after the current
  feature. Do not create a PR.`, must trigger a continuation through
  the prompt-bound `sdlc-start run` command so the coordinator can persist the
  paused or no-PR decision.
- An unfinished active run without a valid prompt binding stops with
  `WORKFLOW_UPGRADE_REQUIRED`; the hook must not synthesize a prompt or emit a
  bare resume command.
- The PreToolUse hook does not block file targets by path. Repo files,
  outside-repo files, credential directories, Codex runtime files, global
  `AGENTS.md`, locked SDLC plans, `$CODEX_HOME/task-state`, and private SDLC
  state are all path-allowed for operator flexibility.
- A user-level registration is matched by tool name before the hook can inspect
  SDLC state. When no active run covers the current working directory, the
  PreToolUse hook exits successfully without applying SDLC policy. Keep its
  optional registration free of a static SDLC status message so ordinary tasks
  are not mislabeled in the UI.
- The PreToolUse hook must still block secret-bearing payloads, dangerous shell
  patterns in Bash tool payloads, and guarded Git/GitHub actions during an
  active run. Patch content that merely contains a shell command is not
  executed and remains governed by patch target, secret, and spec checks.
  Ordinary outbound network commands are not restricted by the hook unless
  they match one of those unsafe checks.
  Public Nebius profile, project, and credential-file path assignments are
  metadata rather than secret material. All other long Nebius assignments,
  including token material, remain fail-closed.
- An active run also covers coordinator-registered integration and worker
  worktrees outside the original checkout. The hook verifies canonical Git
  root/common directory, branch, and recorded HEAD before sensitive Git actions.
- Raw worker commits, integration merges, non-force resource cleanup, and
  ff-only feature promotion require short-lived action-scoped authorization
  matching the worktree, branch, common directory, expected HEAD, expiry, and
  exact command/target. Force cleanup and identity drift remain denied.
- Active-run Git pushes, `gh pr create`, and GitHub PR-creation MCP calls
  require short-lived `create-pr` authorization whose branch and expected HEAD
  match the clean promoted checkout and whose UAT status is `passed`. GitHub PR
  reads and review inspection remain read-only and do not consume PR-creation
  authorization. The push must target exactly `origin` plus one
  `HEAD:<authorized-branch>` refspec; CLI `--head` and MCP `head` values must
  name that same branch, and extra refs are denied. Sensitive Git/GitHub
  actions must be one direct shell action; executable wrappers, absolute
  executable paths, prepended commands, shell operators, and redirections are
  denied.
- The classifier evaluates the literal tool payload. It is not a shell sandbox
  and cannot prove the behavior of a guarded action synthesized indirectly
  through variable expansion, command substitution, an interpreter, or another
  generated program. Workflow skills must issue only the canonical direct
  commands and must not construct guarded actions indirectly.
- Active-run PR merge requires one exact, expiring `sdlc-merge-pr`
  authorization bound to the branch, current promoted/reviewed HEAD, explicit
  user request, passing checks/review/UAT, explicit PR number or URL, and
  complete `gh pr merge ... --match-head-commit <HEAD>` command. The canonical
  command permits only an optional long-form merge strategy; admin bypass,
  branch deletion, repository override, implicit PR selection, shell operators,
  and appended actions are denied. GitHub merge MCP writes are denied in favor
  of this race-safe canonical path.

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
./install-skills.sh --install-all-hooks --register-hooks --refresh-hook-registrations
./install-skills.sh --install-hooks sdlc-start/assets/hooks --register-hooks --refresh-hook-registrations
```

Set `CODEX_HOME` first to target a non-default Codex home. The all-hooks form
syncs every reviewed hook-only bundle under the source skills folder. The
single-source form copies only the SDLC hook scripts and tests into
`${CODEX_HOME:-$HOME/.codex}/hooks`. Add `--register-hooks` when the installer
should merge the SDLC `PreToolUse` and `Stop` entries into `hooks.json`.
`--refresh-hook-registrations` replaces only a differing same-event,
same-script SDLC registration with the same handlers, allowing only
`statusMessage` metadata to differ and preserving unrelated entries; use it
when refreshing an existing registration. Add
`--replace-hooks-json` only when intentionally replacing `hooks.json` with a
clean file built from the selected source manifests. Registration is
idempotent, does not trust hooks or install skills, refuses duplicate Python
hook files within the same hook event, and reports extra installed hook files
or `hooks.json` entries for manual review instead of removing them by default.
After syncing, restart Codex and review the PreToolUse and Stop hook entries in
`/hooks`.
