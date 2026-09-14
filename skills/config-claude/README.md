# Config Claude

An explicit setup skill for Claude Code, usable from Codex or Claude. It
preserves personal settings while reconciling native instructions, three
read-only roles, shared hooks and private task-state storage. The source folder
is a sibling of `config-codex`; neither skill redirects the other's home.

Invoke `$config-claude` from Codex, `/config-claude` for a direct Claude skill,
or `/skills:config-claude` from the repository's Claude plugin. Describe whether
you want inspection, setup, reconciliation or missing-settings recovery. Help
performs no inspection. Installing the skill does not configure the machine.

## Behavior

- Existing `CLAUDE.md` and `settings.json` receive narrow patches. Personal
  content, values and formatting remain intact. Aligned reruns make no changes
  or extra backups.
- Recovery creates missing settings exclusively with private permissions;
  existing or concurrently created files are not replaced.
- The trusted-local full-access profile, delegation policy, Task Implementer
  storage and each MCP integration are optional and explicitly selected.
- Roles use native Markdown with inherited models and only Read/Grep/Glob
  tools. Effective project, CLI and managed overrides need runtime verification.
- Structural checks reject malformed role frontmatter and require task-state
  and selected Task Implementer storage roots to be actual private directories.
- Full hook setup reuses the existing complete package and installer, or the
  enabled native plugin. This skill adds no duplicate hook bundle.
- Fresh setup creates a disabled delegation policy before hook installation
  unless delegation was selected. Reinstallations preserve operator policy.

## Resources

- [Skill contract](SKILL.md)
- [Native setup and checker options](references/local-setup.md)
- [Missing-settings recovery](references/config-recovery.md)
- [Selected MCP integrations](references/mcp.md)

Use the repository installation instructions for local, plugin or skills-CLI
delivery. A standalone copy supports native inspection/recovery, but complete
hook setup must first resolve the full package dependencies. The local
installer still defaults to Codex; `--agent claude` selects Claude.

## Verification

From the repository's `skills/` directory:

```bash
python3 -B config-claude/scripts/test-check-local-idempotency.py
python3 -B global-context-management/scripts/test-claude-roles.py
python3 -B align-skill/scripts/validate-skill-structure.py \
  --agent codex --require-evals config-claude
python3 -B align-skill/scripts/validate-skill-structure.py \
  --agent claude --require-evals config-claude
```

The checker reports structural convergence only. Unit tests use disposable
homes; trigger and quality definitions are shared across both invoking hosts.
Actual role/hook execution requires a fresh native session. Comparative quality
requires an independent baseline; unavailable authenticated runners are not
reported as passing. Configuration applied by an agent remains distinct from
the read-only checker and the deterministic recovery helper.

The shared case files express invocation intent using Codex's dollar form.
For native Claude trials, submit the same case arguments through
`/config-claude` (or `/skills:config-claude` for the plugin). This explicit-only
skill is hidden from automatic selection. The shared print-mode runner's
dollar-form probe does not establish native slash-command dispatch; record
that loading evidence separately before claiming a Claude runtime pass.
See [native invocation control](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill).
