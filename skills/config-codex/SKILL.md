---
name: config-codex
description: "Use only when explicitly asked to configure or recover a public-safe Codex home: config, global policy, MCP, hooks, task state, read-only agents, workspace access, and validation. Never copy private state or secrets."
---

# Config Codex

## Help

For `$config-codex --help` or `$config-codex -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Use this skill to bootstrap or align a user's local Codex runtime setup from
public-safe templates. The goal is a reusable `$CODEX_HOME` layout with global
instructions, features, MCP servers, hooks, task-state storage, and custom
read-only agents that support `global-context-management`. A missing
`config.toml` can be recreated from a reviewed public-safe recovery baseline;
private, plugin-managed, generated, and machine-specific state remains outside
that baseline.

## Use This Skill For

- Creating or updating a local `$CODEX_HOME` layout.
- Recovering a missing `config.toml` from the sanitized create-only baseline.
- Adding global `AGENTS.md` guidance without replacing unrelated user rules.
- Patching `config.toml` for supported Codex features, MCP servers, custom
  agent config layers, and skill discovery.
- Installing hook and custom-agent templates for global context management.
- Opting in to private prompt-workspace storage and write access for
  `task-implementer` without relaxing existing sandbox or approval policy.
- Validating that the resulting local Codex config parses and is ready for
  hook review.
- Re-running against an already configured laptop and reporting "no changes"
  when the required AGENTS, config, hook, and read-only agent surfaces already
  match the intended setup.

## Safety Rules

- Never copy a live local config verbatim into a public repository. When the
  user explicitly asks to refresh the recovery baseline, parse the live file
  without printing it and rebuild the baseline from an allowlist of documented,
  portable settings.
- Never persist personal names, absolute home paths, secrets, tokens, private
  URLs, customer data, raw prompts, command output, stack traces, or broad
  environment dumps.
- Omit personal project lists, shell-environment values, notification commands,
  private or plugin-managed MCP servers, plugin/marketplace state, desktop
  preferences, generated notice/TUI state, inline hook state already owned by
  `hooks.json`, and undocumented feature keys from public recovery assets.
- Store secret values only in the user's shell, password manager, or external
  secret manager. In `config.toml`, reference secret variable names such as
  `CONTEXT7_API_KEY` or `GITHUB_TOKEN`.
- Inspect and build a patch plan before writing. Back up an existing local file
  only when that file will actually be changed.
- `AGENTS.md` and `config.toml` are patch-only when they already exist. Create
  them from templates only when they are missing.
- Do not overwrite, replace, reformat, sort, or regenerate an existing
  `AGENTS.md` or `config.toml`.
- Do not treat `config.toml.template` as desired state for existing machines.
  It is the create-only public-safe recovery baseline plus examples of
  supported settings.
- For hooks, custom-agent TOML files, and optional policy files, copy missing
  files directly from templates. Replace an existing file only when it still
  matches the previous template, or after showing the diff and receiving user
  confirmation.
- Treat full-access settings as intended only for trusted local developer
  machines.
- Never evade a guard by choosing an alternate writer, using shell redirection,
  editing only the installed payload, changing the working directory to escape
  classification, or attempting to disable or unregister it.
- If a repo-owned guard blocks an otherwise safe local Codex patch, first prove
  the exact denying registration, canonical source, installed provenance, and
  documented ownership boundary. When the request authorizes that repair,
  reproduce the false denial in a focused test, repair canonical source first,
  validate it, sync it through the documented installer, report the restart and
  trust boundary, then retry the identical authorized edit.
- If the denial is external or unrepairable in the current scope, including an
  OS, sandbox, enterprise, unknown-provenance, conflicting, or out-of-authority
  policy, report the smallest manual out-of-band action and do not bypass it.
- Never create the optional task-implementer directory inside a Git worktree,
  follow a symlink for it, expose prompt contents, or loosen its `0700` mode.

## Patch-Only Contract

For existing `$CODEX_HOME/AGENTS.md`:

- Preserve all unrelated user rules and ordering.
- If the whole file already matches `assets/AGENTS.md.template`, leave it
  unchanged and do not create a backup.
- Add a compact `config-codex` managed section only if the equivalent guidance
  is missing.
- Keep the task-owned temporary cleanup rule, live-product-validation
  invariant, nested-project instruction precedence/conflict policy, and
  task-state marker preservation in that managed section so existing
  installations receive the same durable policy as a newly rendered
  `AGENTS.md.template` without replacing user-owned content.
- Keep live-product guidance compact: global instructions freeze each trial's
  declared workflow, distinguish product execution from intervention, preserve
  production and high-impact approval boundaries, and require owner-correct
  repair plus clean replay from before the earliest product divergence or
  contamination. Observation is non-intervening only when it cannot affect the
  tested state or execution. Detailed trial, lineage, and reporting rules
  remain in `troubleshoot`.
- Require exactly one active live-product-validation heading across the
  complete `AGENTS.md`; recognize ATX and Setext headings, ignore fenced code
  examples, and reject duplicate or override-like headings outside the managed
  section.
- Do not duplicate `troubleshoot` attempt limits, retry-admission rules,
  blocker-tranche semantics, or exhaustion reporting in global `AGENTS.md`.
  The skill owns those workflow semantics and its separately owned optional
  UserPromptSubmit/PreToolUse/Stop hook owns explicit budget authorization and
  mechanical enforcement. Global context retains only marker-preservation
  continuity for task-state rewrites.
- If managed markers already exist, update only the content between those
  markers.
- Treat empty or stale managed markers as incomplete; update the managed block
  content rather than accepting marker presence alone.
- Do not delete, rewrite, or deduplicate user-authored sections outside the
  managed block.

For existing `$CODEX_HOME/config.toml`:

- Parse the file before editing.
- Add only missing keys, tables, or `[[skills.config]]` entries that are
  required for the requested setup and absent from the current config.
- Preserve existing user values, comments, profiles, project trust entries,
  MCP servers, app settings, and unrelated feature flags.
- Do not add template-only preferences such as model defaults, app/plugin
  settings, MCP servers, project trust entries, `[[skills.config]]` entries, or
  writable roots when the current config already provides the requested
  behavior or the user did not ask for that integration.
- Add `$CODEX_HOME/task-implementer` to
  `sandbox_workspace_write.writable_roots` only when the user explicitly opts
  in and the existing `sandbox_mode` is `workspace-write`. Preserve every
  existing writable root and never change `sandbox_mode` or `approval_policy`
  as part of this opt-in.
- Treat `hooks = true`, `multi_agent = true`,
  `agents.max_concurrent_threads_per_session`, and the three read-only
  custom-agent config references as the minimal config surface for global
  context management. Do not add the legacy `agents.max_threads` alias or the
  undocumented `agents.max_depth` key.
- Require every standalone custom-agent TOML to have a non-empty `name`,
  `description`, and `developer_instructions`; keep `name` and `description`
  aligned with its `[agents.<name>]` declaration and keep `sandbox_mode` set to
  `read-only`. Require each configured target to be a regular non-symlink file
  that resolves inside Codex home.
- Treat explicit `[[skills.config]]` entries for `global-context-management`
  and `config-codex` as optional when the skills are already discoverable from
  the installed user skills directory.
- Do not silently change stricter approval or sandbox settings. Report the
  difference and ask before switching to the trusted-machine full-access
  profile.
- If a target MCP server, custom agent, feature flag, or skill entry already
  exists with different values, report the conflict and patch only after the
  user confirms the desired value.

Read `references/config-recovery.md` before creating a missing `config.toml` or
refreshing the recovery baseline from a live setup. It owns the allowlist,
omissions, redacted inspection procedure, file-mode rule, and private-state
recovery boundary.

## Workflow

1. Identify the target `$CODEX_HOME`; default to `$HOME/.codex`.
2. Identify the installed skills directory; default to `$HOME/.agents/skills`.
3. Inspect existing local Codex files with redaction. Do not print secrets. If
   the recovery baseline is being refreshed, use only the structural allowlist
   procedure in `references/config-recovery.md`; never emit a raw or
   mechanically redacted live-config dump.
4. Run an idempotency preflight. If existing files already satisfy the required
   setup, report that no local changes are needed and stop without creating
   backups.
5. Back up only files that the patch plan will change.
6. Create missing local files from templates, but patch existing
   `AGENTS.md` and `config.toml` according to the patch-only contract. Create a
   missing `config.toml` with `scripts/create-recovery-config.py`, which
   renders `assets/config.toml.template` through an exclusive no-clobber write,
   rejects a symlink target, and sets mode `0600`. If the target appears before
   the write, stop and switch to patch-only handling. If the renderer reports a
   post-publication durability or cleanup warning, do not retry creation; run
   the read-only preflight against the completed file.
7. Create the local layout:
   - `$CODEX_HOME/hooks/`
   - `$CODEX_HOME/agents/`
   - `$CODEX_HOME/task-state/`
   - optional `$CODEX_HOME/hooks/global_context_policy.json` only when the
     user deliberately wants hook-assisted read-only subagent delegation
   - optional `$CODEX_HOME/task-implementer/` with mode `0700` only when the
     user deliberately requests the private prompt-workspace integration
8. Render or adapt templates from `assets/`:
   - `AGENTS.md.template`
   - `config.toml.template`, the public-safe missing-config recovery baseline
   - `hooks.json.template`
   - shared task-state helper template
   - hook script templates
   - optional hook policy template
   - custom-agent TOML templates
   - task-state template
   - the missing-config renderer
   The task-state template must keep `current.md` as a compact rolling
   summary, not an append-only transcript: replace stale details with the
   latest validated state, omit raw logs and secrets, and summarize oversized
   historical task-state files before relying on them.
   Prompt-time hooks may list bounded same-workspace prior task-state candidate
   paths for complex prompts, but must not inject historical task-state
   contents. The parent agent should read only relevant candidates as stale
   hints, verify them against current repo or runtime evidence, and keep the
   current session's advertised `current.md` as the write target.
   Normal startup must remain lazy. Compaction and the first complex prompt may
   create only an empty scaffold with `0700` directories and a `0600` file;
   the parent agent owns semantic state updates.
   For hook scripts, custom-agent assets, and optional policy assets, use
   replace-if-unmodified behavior:
   copy missing files, leave matching files unchanged, and stop for review when
   an existing file differs from both the old and new expected content.
   For `hooks.json`, verify the required global `SessionStart` and
   `UserPromptSubmit` entries are present, but preserve additional workflow hook
   entries such as SDLC `PreToolUse` and `Stop`.
   The root `install-skills.sh --install-all-hooks` helper can install reviewed
   hook-only bundles when that is explicitly needed. Use
   `install-skills.sh --install-hooks config-codex/assets/hooks` only for a
   single-bundle install. Both paths strip `.template` from installed hook file
   names, copy missing hook files, leave matching hook files unchanged, record
   hook file provenance hashes, and back up differing existing hook files before
   refreshing them from source. Add `--register-hooks` only when the operator
   explicitly wants the installer to semantically merge the
   bundle's hook registration into `hooks.json`; add
   `--refresh-hook-registrations` only when differing registrations for the
   same event/script and handlers, allowing only `statusMessage` metadata to
   differ, should be replaced while unrelated entries remain intact. Add
   `--replace-hooks-json` only when the selected source manifests should
   replace `hooks.json` after backup. Registration is validated before payload
   sync.
   Neither path trusts hooks, patches `config.toml`, replaces `AGENTS.md`, or
   replaces this full setup workflow.
9. Confirm `global-context-management` and `config-codex` are installed,
   discoverable, or explicitly enabled as skill folders. Do not add explicit
   skill entries if discovery already works.
10. Validate local hook scripts, TOML, JSON, feature flags, idempotency, and
    secret hygiene. For missing-config recovery, put `--strict-config` on the
    required read-only `codex exec` runtime probe so the installed Codex
    version rejects unknown keys. Audit the full nested task-state tree
    read-only; use the helper's explicit `repair-permissions --execute` only
    after the operator approves a content-preserving one-time mode repair. When
    prompt-workspace integration was requested, run the idempotency preflight
    with `--require-task-implementer-workspace`.
11. Produce an alignment report that lists each checked surface as `Aligned`,
    `Not aligned`, `Repo-owned guard repair pending`, or `Blocked external or
    unrepairable`. For a repo-owned defect, report canonical source, installed
    provenance, validation, install, restart, and identical authorized edit
    retry status. Give exact manual out-of-band remediation only for a genuinely
    external or unrepairable item. Include the minimal file/scope, whether Codex
    attempted the patch, whether a backup was made, and files left untouched.
12. Tell the user to restart Codex, open `/hooks`, review the two
    global-context hooks, and trust them only after confirming the paths are
    expected. If other workflows add their own hooks, review those separately
    and keep event ownership distinct.

## Template Rules

Use placeholders in public assets:

- `{{CODEX_HOME}}` for the user's Codex home in rendered TOML and text files.
- `{{PROJECT_ROOT}}` for the user's trusted repository root.
- `${CODEX_HOME:-$HOME/.codex}` inside `hooks.json.template`, so hook commands
  can resolve against the active shell environment without publishing or
  rendering a machine-specific absolute path.

When writing rendered local files, replace template placeholders with that
user's real paths where the target file needs literal paths. Do not commit
rendered files. Treat full-file templates as source material for missing files;
for existing `AGENTS.md` and `config.toml`, extract and patch only the missing
sections or keys.

Do not add a live project's full trust list, private or plugin-managed
integrations, desktop state, notification command, generated state, or unknown
config keys to the public template. Restore those through their owning local
workflows and report them as intentionally outside public recovery.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Validation

Use the focused checks in `references/local-setup.md`. At minimum:

- Run `python3 scripts/check-local-idempotency.py` for normal laptop setup.
  This script is read-only, validates the merge-safe no-change contract, and
  validates the current `AGENTS.md` managed block content when exact template
  parity is not present. It allows extra reviewed hook registrations in
  `hooks.json` when the required global hooks are present. Use
  `--strict-agents-template` only for explicit canonical template/install-copy
  audits, and use
  `--require-template-mcp-servers` only when the user explicitly wants the
  public MCP baseline audited against the template.
- Add `--require-task-implementer-workspace` only for the explicit private
  prompt-workspace opt-in. It validates that the external directory is outside
  every Git worktree and Git metadata directory, is not a symlink, has `0700`
  mode, and has the matching workspace-write root without printing the real
  Codex home.
  If persistent access is absent or blocked, report exactly:
  `codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"`.
- For source changes to the idempotency script, run
  `python3 scripts/test-check-local-idempotency.py`; it uses disposable local
  fixtures and does not inspect the user's real Codex home.
- Syntax-check hook scripts with non-writing `compile(...)`.
- Parse `config.toml` with `tomllib`.
- Put `--strict-config` on the required read-only `codex exec` runtime probe
  for a newly rendered recovery config. The `features` inspection subcommand
  does not accept this runtime flag.
- Parse `hooks.json` with `json`.
- Confirm `codex features list` reports `hooks` and `multi_agent` enabled.
- Run a targeted secret/path scan over changed public files.

Do not claim runtime activation is proven until a fresh Codex session has
loaded the config, the hooks have been trusted in `/hooks`, and a non-mutating
probe shows the injected task-state path, read/update guidance, and bounded
related prior task-state candidate hints when matching prior summaries exist.

Do not claim subagent activation is proven until a fresh Codex session receives
a prompt request or a user-enabled local hook policy request to use subagents
and can spawn a read-only helper and close that helper before finalizing, or
reports that delegation or close controls are unavailable or not permitted in
that surface.
If delegation is authorized and useful but subagent controls are not visible,
and `tool_search` is available, the fresh session should first search for
multi-agent/subagent tools before reporting delegation unavailable. If a local
hook policy is enabled, verify it in a fresh trusted-hook session before
claiming hook-assisted delegation works. Do not claim that hooks, skills,
`multi_agent`, or `[agents.*]` config directly spawn or close subagents. They
make delegation possible when the runtime policy allows it. After a prompt or
user-enabled local hook policy request authorizes delegation, the fresh session
should dynamically choose and spawn targeted read-only helper roles itself when
useful; before the final response, it should close every spawned helper handle
that is completed or no longer needed when close controls are available, and
report any unavailable or failed cleanup. The prompt does not need to name the
exact role, and the parent agent should not ask for another user prompt only
because the original prompt did not mention subagents.

## References

- Read `README.md` for the human-facing architecture and core concepts.
- Read `references/local-setup.md` before applying the setup to a real machine.
- Read `references/config-recovery.md` before recovering a missing config or
  refreshing the public-safe recovery baseline.
- Use files in `assets/` as templates for rendered local files.

## Output Contract

Return:

- an `Aligned` / `Not aligned` / `Blocked` status list for each checked local
  surface, including at least:
  - `AGENTS.md`
  - `config.toml` feature flags and read-only custom-agent references
  - hook scripts and optional hook policy
  - `hooks.json` required global entries and preserved workflow hooks
  - task-state directory
  - optional task-implementer private directory and workspace access when the
    user requested that integration
- what local files were created or patched
- what backups were made
- what validations passed or failed
- which values still need user-specific replacement
- which private, plugin-managed, generated, or machine-specific config layers
  were intentionally excluded from public recovery
- for every repo-owned guard denial, its owner/provenance evidence, canonical
  source repair, installation/restart state, and identical authorized edit
  retry; for every external or unrepairable denial, the exact manual
  out-of-band action, including the narrow file or bullet to edit and files
  that should be left untouched
- how to restart Codex and trust hooks
- whether optional hook-assisted read-only subagent delegation was enabled
- any remaining risk or unverified runtime behavior

## Hook Event Boundary

This skill's global-context setup owns only `SessionStart` and
`UserPromptSubmit`.

- `SessionStart`: use for global conventions, workspace context, environment
  notes, coding standards, and stable task-state path hints. Do not select
  SDLC phases, modify run state, or inject large documents.
- `UserPromptSubmit`: use only for small global reminders, prompt safety, and
  lightweight context hints. Do not route `sdlc-start`, parse requirements,
  select workflow skills, create run state, or inject large documents.

Workflow-specific guardrails such as Agentic SDLC must use separate reviewed
event hooks, for example `PreToolUse` and `Stop`. A separately owned workflow
may also use its own `UserPromptSubmit` registration for a narrow explicit
authorization syntax and bounded private state; that does not make the global
prompt hook a workflow router.
