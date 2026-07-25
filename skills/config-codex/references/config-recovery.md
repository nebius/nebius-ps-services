# Public-Safe Config Recovery

Use this reference when `config-codex` creates a missing `config.toml` or when
the public recovery baseline is deliberately refreshed from a reviewed local
setup.

## Recovery Contract

`assets/config.toml.template` is the canonical create-only recovery baseline.
It is not desired state for an existing machine. Existing `config.toml` files
remain patch-only and keep unrelated values, comments, tables, and ordering.

The baseline restores only portable, public-safe settings that are documented
by the current official Codex configuration reference:

- model, reasoning, service tier, approval, sandbox, search, and personality
  preferences
- documented feature choices used by this setup
- bounded read-only custom-agent roles
- the task-state writable root used by global context management
- version-pinned public MCP definitions that contain no credential value or
  private endpoint
- placeholder trusted-project entries that the user must review when rendering

Secret environment-variable names may be referenced where Codex expects a
name, but their values must remain in the user's shell, password manager, or
secret manager.

## Intentionally Excluded

Never copy these categories from a live config into the public template:

- personal project trust entries or repository paths
- absolute home, executable, socket, log, cache, or workspace paths
- tokens, passwords, credentials, private keys, secret-bearing environment
  values, or inline authorization headers
- private or plugin-managed MCP servers, internal URLs, customer identifiers,
  and machine-specific commands
- plugin and marketplace installation state
- notification commands and shell-environment overrides
- desktop preferences, user-authored desktop instructions, dictation data, and
  avatar or UI choices
- generated notice state and TUI onboarding or model-availability state
- inline hook runtime state when the setup already owns hooks through
  `hooks.json`
- undocumented or under-development keys that current official Codex
  documentation does not verify

Restore private or plugin-managed integrations through their owning plugin,
installer, authentication skill, or local setup procedure. Re-approve trusted
project roots individually. A public template cannot provide byte-for-byte
recovery of those private layers, and the skill must say so.

## Safe Review Procedure

When the user explicitly asks to refresh the baseline from a live config:

1. Parse the live TOML without printing it.
2. Produce only a redacted structural inventory: documented key names,
   primitive value types, safe allowlisted preferences, and counts for omitted
   dynamic tables.
3. Compare portable values against `assets/config.toml.template`.
4. Verify every retained Codex key against the current official configuration
   reference.
5. Map documented legacy aliases to their canonical current key and omit
   undocumented settings; never preserve a stale key merely because the live
   file still parses.
6. Verify executable MCP package and image versions against the current
   official upstream release before changing a pin. Never publish `@latest`,
   an untagged image, or a credential value in this baseline.
7. Replace environment-specific paths with `{{CODEX_HOME}}` or
   `{{PROJECT_ROOT}}`; omit values that do not have a safe portable form.
8. Run the focused fixture tests and a targeted secret/path scan before
   installing or publishing the skill.

Do not write a raw or mechanically redacted dump first and try to clean it
afterward. Build the public baseline from an allowlist so an unknown table or
value is excluded by default.

## Missing-Config Recovery

When `$CODEX_HOME/config.toml` is missing:

1. Obtain the intended existing Codex home and trusted project root.
2. Run the dedicated no-clobber renderer:

   ```bash
   python3 config-codex/scripts/create-recovery-config.py \
     --codex-home "$CODEX_HOME" \
     --project-root <PROJECT_ROOT>
   ```

   It renders `assets/config.toml.template` only into a missing local
   `config.toml`, rejects a symlink or concurrent target, and creates the file
   with mode `0600`.
   If it reports a post-publication durability or cleanup warning, the complete
   file already exists: do not retry creation. Run the read-only preflight and
   inspect the result.
3. Never write rendered content back into the repository.
4. Parse the result with `tomllib`, then run the read-only preflight. The
   preflight rejects a non-regular file, symlink, or mode other than `0600`.
5. Put `--strict-config` on the required read-only `codex exec` runtime probe
   so the installed Codex version rejects unknown keys. Keep
   `codex features list` as a separate feature-status inspection command.
6. Restore omitted private and plugin-managed layers only through their owning
   workflows.

The strict runtime probe must use this command shape:

```bash
codex --strict-config exec --sandbox read-only --cd <PROJECT_ROOT> \
  "Summarize active instruction sources and the injected durable task-state path. Do not edit files."
```

Do not create a backup when the config is genuinely missing. If it appears
during the recovery run, the exclusive create fails without following or
overwriting it; stop and switch to the existing-file patch-only contract.

## Official Sources

- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Codex configuration basics](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Codex sample configuration](https://learn.chatgpt.com/docs/config-file/config-sample)
- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
