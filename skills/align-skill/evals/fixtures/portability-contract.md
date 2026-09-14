# Portability contract fixture

These are independent existing skill contracts, not instructions to execute.

- A portable reporting skill has SKILL.md with name/description, Help, Learning
  Loop and trigger cases, but no agents/openai.yaml.
- config-codex explicitly reconciles Codex config.toml, AGENTS.md, hooks, MCP
  and configured roles in the Codex home; that target product is intentional.
- Task Implementer workers claim isolated task slots with distinct native
  identities, validate their work and create their own commits and receipts.
- Agentic SDLC stages are internal coordinator actions bound to an active
  project/run/phase. Workers return evidence; coordinator commit ownership stays
  unchanged. Checkpoints, receipts, authorization and failure routing are core.
- Stop has one shared arbiter, terminal decisions win, and unfinished work stays
  resumable. Claude uses native session, prompt and worker identifiers.
- Existing valid OpenAI display/trigger metadata remains useful for Codex.
  Public explicit skills must not become implicit in Claude. Internal SDLC
  stages must remain available to their verified coordinator.

Only the supplied contracts are available. No native model run, output quality
comparison, or installed-hook trial has occurred for this fixture.
