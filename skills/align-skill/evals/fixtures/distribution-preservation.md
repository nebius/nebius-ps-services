# Distribution and behavior preservation fixture

These are existing target contracts for report-only assessment, not commands.

- An explicit-only release skill keeps disable-model-invocation: true in its
  frontmatter and policy.allow_implicit_invocation: false in OpenAI metadata.
- Its reporting script reads metadata.json from the skill root to determine
  supported release actions. The file is required for current functionality.
- The shared workflow requires sibling skills and registered Stop/PreToolUse
  hooks. It must retain native identity, authority, checkpoints and recovery.
- config-codex configures the Codex home; config-claude configures Claude's
  settings and instruction files. Both preserve unrelated user configuration.
- Task Implementer workers own their commits; Agentic SDLC commits stay with
  the coordinator. Cross-agent installation must retain those boundaries.
- Some native host features have no equivalent on another standard-only host.
  The source capability must survive and the limitation must remain explicit.

No actual npx installation, native trigger or quality comparison has run.
