# Local Setup Reference

Use this reference when applying `config-codex` to a real machine. Public repo
files must stay generic; rendered local files belong under that user's
`$CODEX_HOME`.

Read `config-recovery.md` before creating a missing `config.toml` or refreshing
the public recovery baseline from a reviewed local setup.

## Inputs

Collect these values first:

```text
CODEX_HOME=${CODEX_HOME:-$HOME/.codex}
SKILLS_HOME=$HOME/.agents/skills
PROJECT_ROOT=<PROJECT_ROOT>
```

Use real paths only in local rendered files and commands. Do not commit them.

## Idempotency First

Before writing anything, inspect the current local setup and build a patch
plan. If no file needs to change, do not create backups and do not rewrite
files just to match template formatting.

For a normal laptop setup, run the merge-safe preflight:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$CODEX_HOME"
```

If the check passes, report that no local changes are required for the checked
surfaces. If it fails, patch only the failed surfaces.
Use `--strict-agents-template` only for explicit canonical
template/install-copy audits, and use `--require-template-mcp-servers` only
when the user explicitly wants the public MCP baseline audited against
`assets/config.toml.template`. These audit flags must not be used as
justification to replace existing laptop `AGENTS.md` or `config.toml` files.

Use `--require-task-implementer-workspace` only after the user explicitly
opts in to private prompt-workspace access. The default preflight must remain
independent of that optional directory.

When a repo-owned guard blocks an otherwise safe patch, prove its exact
registration, canonical source, installed provenance, and documented ownership
boundary. If the request authorizes repair, reproduce the false denial in a
focused test, repair canonical source first, validate it, sync through the
documented installer, report the restart/trust boundary, and retry the
identical authorized edit. Never use an alternate writer, shell redirection,
an installed-only edit, a working directory escape, or an attempt to disable
or unregister the guard. For an external or unrepairable OS, sandbox,
enterprise, unknown-provenance, conflicting, or out-of-authority denial,
report the blocked file, smallest intended edit, and narrow manual out-of-band
action. Also name aligned files that should not be touched.

## Backup

Back up only files that the patch plan will change. Do not create backup files
for a no-op run.

Example backup helper for the files that will be changed:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
ts="$(date +%Y%m%d%H%M%S)"

mkdir -p "$CODEX_HOME"

for file in "$@"; do
  if [ -f "$CODEX_HOME/$file" ]; then
    cp "$CODEX_HOME/$file" "$CODEX_HOME/$file.bak.$ts"
  fi
done
```

## Directory Layout

Create the runtime directories:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

mkdir -p "$CODEX_HOME/hooks" "$CODEX_HOME/agents" "$CODEX_HOME/task-state"
chmod 700 "$CODEX_HOME/task-state"
```

## Optional Task Implementer Prompt Workspace

This integration is opt-in. When requested, create the private storage root
outside every Git worktree and Git metadata directory, and restrict it before
any prompt is stored:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

if [ -L "$CODEX_HOME/task-implementer" ]; then
  echo "Refusing symlinked task-implementer storage" >&2
  exit 1
fi

mkdir -p "$CODEX_HOME/task-implementer"
chmod 700 "$CODEX_HOME/task-implementer"
```

Do not accept a symlink at that path. Do not print prompt bodies while
inspecting or validating it.

If the existing `sandbox_mode` is `workspace-write`, append the rendered
absolute path to the existing root list without deleting or reordering other
entries:

```toml
[sandbox_workspace_write]
writable_roots = ["<existing-root>", "{{CODEX_HOME}}/task-implementer"]
```

Replace `{{CODEX_HOME}}` only in the user's local file. Do not add this root
when the user did not request the integration. When the current sandbox is
`danger-full-access`, no writable-root patch is needed. When the current
sandbox is stricter, the config patch is blocked, or a one-session grant is
preferred, leave `sandbox_mode` and `approval_policy` unchanged and report
this exact generic invocation:

```bash
codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"
```

Validate the opt-in contract without exposing the resolved home path:

```bash
python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$CODEX_HOME" \
  --require-task-implementer-workspace
```

The validator checks that the directory is real, is not a symlink, is outside
every Git worktree and Git metadata directory, and has mode `0700`. Under `workspace-write`, it also
requires the canonical directory in
`sandbox_workspace_write.writable_roots`. The validator never modifies local
files or policy.

## Create Or Patch From Templates

Use the files in `assets/` as source templates. The arrows below describe the
source for missing files; existing files follow the merge and
replace-if-unmodified rules after the list.

```text
assets/AGENTS.md.template              -> $CODEX_HOME/AGENTS.md
assets/config.toml.template            -> $CODEX_HOME/config.toml
assets/hooks.json.template             -> $CODEX_HOME/hooks.json
assets/hooks/global_context_state.py.template
  -> $CODEX_HOME/hooks/global_context_state.py
assets/hooks/session_start_context.py.template
  -> $CODEX_HOME/hooks/session_start_context.py
assets/hooks/user_prompt_context.py.template
  -> $CODEX_HOME/hooks/user_prompt_context.py
Optional local hook policy:
assets/hooks/global_context_policy.json.template
  -> $CODEX_HOME/hooks/global_context_policy.json
assets/agents/repo_mapper.toml.template
  -> $CODEX_HOME/agents/repo_mapper.toml
assets/agents/test_strategist.toml.template
  -> $CODEX_HOME/agents/test_strategist.toml
assets/agents/risk_reviewer.toml.template
  -> $CODEX_HOME/agents/risk_reviewer.toml
```

Every standalone custom-agent TOML must contain a non-empty `name`,
`description`, and `developer_instructions`. The `name` and `description` must
match its `[agents.<name>]` declaration in `config.toml`, and `sandbox_mode`
must remain `read-only`. Each configured target must be a regular non-symlink
file that resolves inside Codex home.

For hook scripts, custom-agent TOML files, and optional policy files, use
replace-if-unmodified behavior:

- Copy the file when the target is missing.
- Leave the file unchanged when the target already matches the current
  template.
- Replace the file only when it still matches the previous known template, or
  after the user reviews the diff and confirms the local customization should
  be discarded.

Replace placeholders:

- `{{CODEX_HOME}}` with the user's Codex home.
- `{{PROJECT_ROOT}}` with the user's trusted project root.

The `hooks.json` template intentionally uses
`${CODEX_HOME:-$HOME/.codex}` directly, so the hook commands stay portable when
the user sets `CODEX_HOME` in the shell before starting Codex.

Treat `hooks.json` as a semantic merge target on existing machines. Ensure the
global-context `SessionStart` and `UserPromptSubmit` entries from the template
are present, but preserve additional reviewed workflow entries such as Agentic
SDLC `PreToolUse` and `Stop` hooks. Do not replace `hooks.json` just to match
the template byte-for-byte.

The root `install-skills.sh --register-hooks` path follows the same semantic
merge model for hook bundles by default: it validates `hooks.json` before
payload sync, preserves existing entries, and appends only missing source
entries. It also refuses duplicate Python hook files within the same hook event
so stale variants cannot silently run alongside current registrations. Hook
file installation copies missing files, leaves matching files unchanged, and
records hook file provenance hashes. It backs up differing existing hook files,
then refreshes them from the selected source. It still does not trust hooks or
patch `config.toml`. Add
`--refresh-hook-registrations` when differing registrations for the same
event/script and handlers, allowing only `statusMessage` metadata to differ,
should be replaced while unrelated entries remain.
Add
`--replace-hooks-json` only when the operator intentionally wants to back up
and replace `hooks.json` with a clean file built from the selected source
manifests. Hook install modes are idempotent and report extra installed hook
files or `hooks.json` entries that are not present in the selected source
manifests; those reports are advisory and do not delete files or edit existing
registrations unless `--replace-hooks-json` is explicitly set.

If `$CODEX_HOME/AGENTS.md` is missing, create it from
`assets/AGENTS.md.template`. If it exists, do not replace it. Append or update a
small managed section for `config-codex`/`global-context-management` guidance
and leave unrelated user rules untouched. Marker presence alone is not enough:
empty or stale managed blocks must be updated in place.
The complete file must contain exactly one active live-product-validation
heading. ATX and Setext headings are recognized; fenced code examples are
ignored. Duplicate or override-like headings outside the managed section fail
preflight.

The managed section must include exactly one canonical copy of the compact
live-product-validation invariant from `assets/AGENTS.md.template`. It must keep
declared product execution separate from fixture setup and recovery, freeze
each trial declaration, mark out-of-band execution, bypass, or pre-satisfaction
of a product-owned step as intervened evidence, classify nominally read-only
actions by their criterion-relevant effects, and retain production and
high-impact action-approval boundaries. It requires owner-correct repair and
permits a verified-fix claim only after clean replay from a declared or
independently proven known-good checkpoint before the earliest product
divergence or contamination, with quiescent prior writers and independent
postconditions. Detailed trial and reporting rules belong to `troubleshoot`,
not the global file.

The managed section must not duplicate `troubleshoot` attempt limits,
retry-admission rules, blocker-tranche semantics, or exhaustion reporting.
Those workflow semantics belong to the skill; its separately owned optional
UserPromptSubmit/PreToolUse/Stop hook owns explicit budget authorization,
mechanical validation, and enforcement. Keeping a second copy in global
instructions allows policy drift and can cause a compliant agent to author a
marker the current guard rejects. Preserve the active
`codex-remediation-budget:v1` marker while rewriting private task state; this is
continuity guidance only and does not define the marker's limits or lifecycle.

The managed section must also permit agents to clean up temporary trees they
created during the current task. Require the exact task-specific path to be
resolved and validated under the system temporary directory first, use scoped
non-forced deletion such as `find "$task_temp_dir" -depth -delete`, and never
target the temporary root or an unresolved variable.

The managed section must include the nested-project instruction contract from
`assets/AGENTS.md.template`: resolve the selected project, read every
applicable instruction file from repository root through that project, retain
root and ancestor rules, never weaken higher-level security or destructive
operation safeguards, stop on irreconcilable conflicts, explicitly read a
newly generated `AGENTS.md` in the current session, and treat
`AGENTS.override.md` as active without creating one automatically.

Recommended managed block markers:

```markdown
<!-- BEGIN config-codex managed context -->
...
<!-- END config-codex managed context -->
```

If `$CODEX_HOME/config.toml` is missing, create it through the public-safe
no-clobber renderer:

```bash
python3 config-codex/scripts/create-recovery-config.py \
  --codex-home "$CODEX_HOME" \
  --project-root <PROJECT_ROOT>
```

The renderer uses `assets/config.toml.template`, rejects a symlink or
concurrent target, validates the rendered TOML, and creates the file with mode
`0600`. Do not create a backup for a file that did not exist. Run the required
read-only `codex exec` runtime probe with `--strict-config` after rendering.
If the target appears before the exclusive write, stop and follow the
existing-file patch-only contract. If it exists from the start, do not replace
it. A post-publication durability or cleanup warning means the complete file
already exists; do not retry creation, and run the read-only preflight. Parse
an existing file first, then add only missing settings required by the
requested setup:

- `[features]` entries such as `hooks` and `multi_agent`.
- `[agents]` limits and `[agents.<name>]` custom-agent references.
- MCP server tables only for integrations the user explicitly requested.
- `[[skills.config]]` entries only when skill discovery is not already working
  and the user wants explicit entries.
- Trusted project entries only for project roots the user explicitly wants.

Preserve existing user values, comments, profiles, project trust entries, MCP
servers, app settings, and unrelated feature flags. If an existing value
conflicts with the template, report the difference and ask before changing it.
Do not silently relax approval or sandbox settings.

Do not add template-only model defaults, app/plugin settings, MCP servers,
project entries, skill entries, or writable roots to an existing config merely
because they appear in `assets/config.toml.template`.

The private task-implementer root is the exception only after explicit opt-in;
follow the dedicated contract above and preserve the existing policy.

For a missing config, the public-safe MCP baseline in the template restores the
reusable MCP servers that can be expressed without private values. Executable
package and container references are pinned to reviewed upstream releases;
never replace them with `@latest` or an untagged image. Existing configs may
also contain plugin-managed or machine-specific MCP servers with absolute
commands or private environment values. Preserve those entries during
patching, but restore them through their owning plugin or setup skill rather
than copying local values into public templates.

The recovery baseline also omits personal project lists, secret-bearing shell
values, notification commands, desktop preferences, plugin and marketplace
state, generated notice/TUI state, inline hook state already owned by
`hooks.json`, and undocumented keys. Re-approve project roots and restore each
private layer through its owning local workflow. Do not claim byte-for-byte
recovery from the public template.

## Optional Hook-Assisted Subagent Policy

To have the `UserPromptSubmit` hook add a lightweight hint about configured
read-only subagents for complex prompts, create this local-only file:

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

Save it as `$CODEX_HOME/hooks/global_context_policy.json`. The optional policy
template is enabled because creating this local file is the deliberate opt-in.
The public templates do not hardcode agent names for this path. The hook reads
`$CODEX_HOME/config.toml`, discovers `[agents.<name>]` entries whose referenced
config files have `sandbox_mode = "read-only"`, and injects those agent names
into model-visible context as a bounded read-only delegation request. It does
not inject local agent config paths, and it does not directly call the subagent
tool. Treat that local-policy request as sufficient authorization for the turn
when the active runtime and instructions accept hook context, so the main agent
dynamically decides whether to spawn the smallest useful set of targeted
read-only helpers. After authorization, the prompt does not need to name a
specific helper role, and the parent agent should not ask for another user
prompt only because the original prompt did not mention subagents.
The parent agent still owns lifecycle cleanup: wait for returned summaries,
consolidate them, and close completed subagent threads when close controls are
available. Before the final response, close every spawned subagent handle that
is completed or no longer needed. If close controls are unavailable or cleanup
fails, report the residual open or running handle instead of leaving it silent.
With multiple subagents, close each completed handle as its terminal result
arrives and continue waiting on the remaining handles.

## Secret Handling

Do not put actual token values in `config.toml`.

For Context7:

```bash
export CONTEXT7_API_KEY="<context7-api-key>"
```

For GitHub MCP:

```bash
export GITHUB_TOKEN="<github-token>"
```

Store those exports in the user's preferred local shell or secret-management
setup. Do not commit them.

## Validation

Run the read-only idempotency preflight:

```bash
python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$CODEX_HOME"
```

The preflight checks the merge-safe laptop contract, including required global
hook registrations as a subset so extra reviewed workflow hooks can coexist in
`hooks.json`. It accepts exact `AGENTS.md` template parity or a current managed
block; empty or stale managed markers fail validation.

Summarize the result as an alignment matrix before making or recommending
changes:

```text
Aligned: <surface and evidence>
Not aligned: <surface and exact drift>
Repo-owned guard repair pending: <owner, provenance, source/install/restart status>
Blocked external or unrepairable: <surface and external policy owner>
Manual out-of-band action: <narrow edit only for an external or unrepairable block>
Identical authorized edit retry: <passed, failed, or pending after owner repair>
Leave untouched: <aligned local files that should not be changed>
```

For example, if only the managed context block in `$CODEX_HOME/AGENTS.md` has a
stale bullet, report the exact stale bullet and tell the user to patch only
that bullet. Do not recommend replacing the whole file, and do not touch
`config.toml`, hooks, hook policy, custom-agent TOMLs, or `hooks.json` when
those surfaces already validate.

Syntax-check rendered hooks without bytecode writes:

```bash
python3 - <<'PY'
import os
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
for path in (
    codex_home / "hooks/session_start_context.py",
    codex_home / "hooks/user_prompt_context.py",
):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("hook scripts parse")
PY
```

Parse TOML:

```bash
python3 - <<'PY'
import os
import tomllib
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
print("config.toml is valid TOML")
PY
```

After creating a missing config from the recovery baseline, put
`--strict-config` on the read-only runtime probe below. Keep
`codex features list` as the separate feature-status check; the `features`
subcommand rejects the runtime strict-config flag in the installed CLI.

Parse hooks JSON:

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
print("hooks.json is valid JSON")
PY
```

Check features:

```bash
codex features list | rg '^(hooks|multi_agent)\s'
```

Expected:

```text
hooks        stable  true
multi_agent  stable  true
```

If needed:

```bash
codex features enable hooks
codex features enable multi_agent
```

## Restart And Trust Hooks

Codex CLI has no separate restart command. Exit:

```text
/quit
```

Then start a fresh session:

```bash
cd <PROJECT_ROOT>
codex
```

Or:

```bash
codex --cd <PROJECT_ROOT>
```

For VS Code, run `Developer: Restart Extension Host` from the Command Palette.

In the fresh session:

```text
/hooks
```

Review the two local hooks and trust them only when the paths point to the
expected scripts under `$CODEX_HOME/hooks/`.

## Runtime Probe

After trusting hooks, run a non-mutating probe:

```bash
codex --strict-config exec --sandbox read-only --cd <PROJECT_ROOT> \
  "Summarize active instruction sources, available skills/custom agents, and the injected durable task-state path. Do not edit files."
```

Expected evidence:

- `global-context-management` is available.
- `config-codex` is available.
- `repo_mapper`, `test_strategist`, and `risk_reviewer` are available, or the
  session first uses `tool_search` to look for deferred multi-agent/subagent
  tools when controls are not visible, then clearly reports that subagent
  delegation is unavailable or not permitted in the current surface.
- The injected task-state path is session-scoped under
  `$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md`.
- Complex-task guidance tells Codex to read current task state when prior
  context may matter, consider bounded same-workspace prior task-state
  candidate paths when matching summaries exist, then keep checkpoint updates
  concise.
  `current.md` should remain a rolling summary, not an append-only transcript;
  stale or oversized historical details should be summarized before use.
  Candidate paths are optional stale hints; hooks must not inject historical
  task-state contents.
  If the optional policy is enabled, it should also mention the discovered
  configured read-only agents by name.
- Normal startup remains lazy. Compaction and the first complex prompt create
  only an empty `0600` scaffold below private `0700` directories; the parent
  writes and updates all semantic content.
- Sandbox configuration permits the intended local writes. A selected-project
  lifecycle guard does not treat task state, config, hooks, installed skills,
  or other user files as its control plane; fixed external writes pass through
  to the operating system, Codex permissions, destructive-action safeguards,
  and any owning domain policy.

Do not run complex synthetic hook probes against a live `$CODEX_HOME`: the
first complex prompt intentionally creates an empty private scaffold. Prefer
`global-context-management/scripts/validate-local-templates.py` for hook-unit
validation because it uses disposable temporary homes. If a hook payload has no
`session_id`, task state is unavailable and no manual or legacy fallback path is
created.
The validator also checks bounded related prior task-state candidate discovery
for same-workspace files and verifies that unrelated workspace files and
historical task-state contents are not injected into hook context.

Then run an explicit subagent probe:

```bash
codex --strict-config exec --sandbox read-only --cd <PROJECT_ROOT> \
  "Use $global-context-management. Explicitly spawn one read-only repo_mapper subagent to inspect this repository. Do not edit files. Wait for it, close it after the result when close controls are available, then report whether the subagent was spawned and closed."
```

If that succeeds but ordinary complex prompts do not spawn subagents, the
configuration is working; the remaining gate is delegation authorization. A
prompt can ask for subagents, delegation, or parallel agents, and the optional
local hook policy can inject a bounded read-only delegation request for complex
prompts after it is enabled and trusted in a fresh session. Treat that policy
request as sufficient authorization when the active runtime and instructions
accept hook context. Once authorization is present, Codex should dynamically
choose and spawn useful targeted roles itself; the prompt does not need to name
the exact role.

If the explicit probe does not see subagent controls but `tool_search` is
available, the agent should search for multi-agent/subagent tools before
reporting delegation unavailable.
