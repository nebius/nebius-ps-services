# Changelog

All notable changes to the reusable Codex skills are tracked here.

## [Unreleased]

### Added

- Added this `skills/CHANGELOG.md` so reusable-skill release notes live with
  the skills instead of in the monorepo root changelog.
- Added the `align` Codex skill for end-to-end project review and repair passes
  that inspect a codebase broadly before editing, then fix inconsistencies
  across implementation, tests, CI, CLI/help output, documentation, and
  formatting instead of only reporting them.
- Added the `align-skill` Codex skill for reviewing and standardizing Codex or
  Agent Skill folders, including `SKILL.md`, references, assets, scripts,
  official vendor-doc verification, safety guardrails, canonical structure, and
  validation evidence.
- Added the `attach-ubuntu` Codex skill with a Bash helper that creates or
  reuses a per-project Ubuntu container, mounts the current project at
  `/workdir`, updates VS Code attached-container defaults, bootstraps Ubuntu
  build tools and Python project dependencies inside the container, isolates
  them in a container-specific virtual environment, preserves Git metadata for
  monorepo subprojects, and best-effort opens a new Dev Containers window for
  the running container.
- Added the `create-pr` Codex skill for branch-safe GitHub PR creation.
- Added the `config-codex` Codex skill for bootstrapping public-safe local
  Codex runtime setup, including global `AGENTS.md`, `config.toml` features
  and MCP servers, hooks, task-state layout, read-only custom agents, and a
  design README.
- Added the `global-context-management` Codex skill for keeping complex Codex
  sessions focused and recoverable with durable task state, concise
  parent-thread discipline, bounded read-only subagents, local-only hook
  templates, final risk review guidance, a local-only template validation
  helper, and a skill-local design README.
- Added skill-local `README.md` files across reusable skills so each skill
  folder has human-facing architecture, workflow, core concept, and file
  responsibility documentation.
- Added the `onboard-nebius-cxcli` Codex skill as the central onboarding guide
  for Nebius Terraform modules that need to be wired into
  `services/nebius-cxcli`, including catalog-first onboarding and optional
  code-owned layers for wizard/provider, runtime validation, status polling,
  and cluster handoff behavior.
- Added the `publish-helm` Codex skill for creating Nebius OCI Helm chart
  publication flows.
- Added the `review-pr` Codex skill for GitHub-backed PR review and
  merge-readiness work.

### Changed

- Clarified the `global-context-management` and `config-codex` contracts so
  subagent delegation is described as runtime- and instruction-dependent,
  noisy parent-thread output is called out as the main context-growth risk, and
  hook prompts now provide a narrow-read fallback when subagents are not
  available or permitted. Task-state files are now explicitly described as
  continuity notes that must be read at task start, resume, or compaction
  boundaries when prior context may matter, then updated at checkpoints. The
  shared hook registration template now consistently resolves scripts through
  `${CODEX_HOME:-$HOME/.codex}`, and the skill READMEs plus `SKILL.md`
  contracts now state the same installer-versus-runtime split.
- Clarified that `multi_agent`, configured `[agents.*]` roles, hooks, and skill
  activation make subagent delegation possible but do not force automatic
  delegation. Current runtime probes must explicitly ask Codex to use or spawn
  subagents, use delegation, or run parallel agents; the `codex exec` examples
  now use the current `--sandbox read-only` flag instead of the obsolete
  `--ask-for-approval` option.
- Added opt-in hook-assisted read-only subagent delegation for
  `global-context-management`: the `UserPromptSubmit` hook can now discover
  configured read-only agents from `$CODEX_HOME/config.toml` and
  `$CODEX_HOME/agents` when a local `$CODEX_HOME/hooks/global_context_policy.json`
  policy enables it, while keeping public templates free of personal paths.
- Clarified the read-only subagent lifecycle: the parent agent should ask
  helpers for concise final summaries, wait for results, consolidate them, and
  close completed subagent threads when close controls are available and no
  follow-up is needed. When multiple helpers are running, the parent should
  close each completed handle as its terminal result arrives and continue
  waiting on the remaining handles.
- Documented the parent/subagent mental model in the `config-codex` and
  `global-context-management` READMEs.
- Clarified that direct live hook probes with synthetic `session_id` values can
  leave scaffold-only task-state directories, and extended local template
  validation to cover the no-`session_id` manual fallback path.
- Aligned the `config-codex` AGENTS template and skills README with the global
  script-execution policy: read-only inspection or validation scripts may run
  when they do not modify files, services, external systems, credentials, or
  persistent state, while mutating scripts still require explicit Run/Execute
  wording.
- Updated the `global-context-management` hook template so hook commands
  resolve scripts through `${CODEX_HOME:-$HOME/.codex}` and work with
  non-default local Codex homes.
- Tightened the `config-codex` skill contract so existing user `AGENTS.md` and
  `config.toml` files are patched in place rather than replaced, with
  conflicts reported instead of silently overwritten.
- Tightened the `helmchart` Codex skill with more accurate `appVersion`
  guidance, dependency-aware strict validation, safer securityContext and RBAC
  mutation rules, trigger-scope metadata, a reusable chart validation helper,
  and lightweight trigger eval prompts.
- Clarified the `align` Codex skill guidance and metadata so alignment passes
  operate as cautious senior code-review style sweeps: audit-first,
  evidence-driven, wiring-aware, conservative around public contracts and
  business logic, and still focused on fixing verified low-risk gaps across
  code, tests, docs, workflows, CLI/help output, config, and applicable project
  skills.
- Updated the `create-pr` guidance and metadata so Codex treats explicit
  user-supplied PR titles and bodies as authoritative instead of inferring a
  generic title from the branch name.
- Expanded `create-pr` so Codex can prepare one or more conflict-free PR
  branches against the default branch, use the current branch as the target
  when no branch is provided, check ordered multi-branch merge paths, preserve
  one PR per branch, and report the manual merge order.
- Tightened `create-pr` so known branch-owned validation, build, lint, test,
  or GitHub check failures are repaired when safe before the PR link is
  presented as the completed outcome.
- Expanded `review-pr` so Codex can review PRs by number, URL, or current
  branch, distinguish user-owned, same-repository, forked, writable, and
  externally owned branches, resolve safe base-branch conflicts only when
  permissions allow, and report exact blockers when a contributor branch cannot
  be updated.
- Renamed the Nebius cxcli onboarding skill to `onboard-nebius-cxcli`, updating
  the skill folder, `SKILL.md` metadata, OpenAI agent metadata, sibling-skill
  routing, repo docs, and nebius-cxcli documentation references.
- Updated `install-skills.sh` so source-owned renamed or removed skills
  converge on reinstall and target-only skills are listed at the end with a
  short default-target `--remove-skill` hint instead of being silently ignored.
  The installer help and README now state the no-argument install and default
  removal targets, plus the custom-destination removal form.
- Aligned the reusable skill set with current skill and workflow conventions:
  `align-skill`, `attach-ubuntu`, and `nebius` now state the repository
  script-execution policy explicitly, while `github-workflows`,
  `publish-image`, and `python-project` templates use current action major
  pins verified from upstream action repositories, and skill reference docs pass
  focused Markdown lint checks.
- Refined `create-pr` and `review-pr` to follow stronger Git and GitHub PR
  best practices: `create-pr` refreshes base-branch context, avoids opening
  PRs from a dirty tree alone, and prefers draft PRs for incomplete work, while
  `review-pr` distinguishes safe local rebases from shared-branch cases that
  should use non-destructive updates, preserves unresolved reviewer concerns as
  explicit blockers, and routes selectively to sibling skills.
- Expanded `skills/README.md` with explicit Codex chat prompt examples,
  including copy-pastable `create-pr` and `review-pr` usage plus the GitHub
  prerequisites those skills expect before they can act.
- Updated the shared `python-project` skill to explain when a minimal
  compatibility `setup.py` shim still makes sense alongside `pyproject.toml`.
- Updated `create-pr` so local-work PR creation explicitly stages the complete
  monorepo diff with `git add -A` before committing, unless the user requests a
  narrower PR scope.
