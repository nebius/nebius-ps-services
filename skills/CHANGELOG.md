# Changelog

All notable changes to the reusable Codex skills are tracked here.

## [Unreleased]

### Added

- Added the Agentic SDLC skill set: `sdlc-create-requirements`, `sdlc-start`,
  `sdlc-gather-context`, `sdlc-create-design`, `sdlc-create-plan`,
  `sdlc-tdd`, `sdlc-implement-plan`, `sdlc-validate-codes`,
  `sdlc-unit-tests`, `sdlc-evaluate`, `sdlc-classify-failure`,
  `sdlc-gui-test`, `sdlc-tui-test`, `sdlc-commit`, `sdlc-uat-tests`,
  `sdlc-merge-pr`, and `sdlc-align-specs`. These skills keep committed
  product truth in `docs/requirements.md` and
  `docs/design.md`, keep execution state under `~/.codex/sdlc-runs`, and
  model the SDLC loop as skill-selected phases rather than a workflow CLI. The
  SDLC flow reuses existing `create-pr` and `review-pr` as PR creation and
  review handoff phases.
- Added SDLC templates for requirements, design, context packs, locked feature
  plans, validation/test/evaluation evidence, local commit evidence, and UAT
  reports, plus state-schema and failure-taxonomy references.
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
- Added the `apply-security` Codex skill for security scans, remediation
  plans, safe patching, verification, and explanation across Terraform,
  Kubernetes, Helm, CI/CD, Bash, Python, Java, JavaScript, TypeScript, and Rust
  codebases.
- Added the `attach-ubuntu` Codex skill with a Bash helper that creates or
  reuses a per-project Ubuntu container, mounts the current project at
  `/workdir`, updates VS Code attached-container defaults, bootstraps Ubuntu
  build tools and Python project dependencies inside the container, isolates
  them in a container-specific virtual environment, preserves Git metadata for
  monorepo subprojects, and best-effort opens a new Dev Containers window for
  the running container.
- Added the `commit-push` Codex skill for committing all current feature-branch
  changes with `git add -A`, pushing the branch to `origin`, and reporting
  final worktree cleanliness without opening a pull request.
- Added the `code-info` Codex skill for copy/paste-friendly project code
  metrics, including LOC by language and component, repo size and link,
  test-file counts, CLI command detection, module/package counts, artifact
  sizes, and available coverage artifacts. It supports local folders and
  remote GitHub repositories that are not cloned locally by reading a temporary
  repository archive with local GitHub token discovery when needed.
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
- Added the `install-grafana-mcp-for-nebius` Codex skill for installing the
  official Grafana MCP server, wiring it into Codex, refreshing a
  Nebius-managed Grafana token file, discovering Grafana datasources, and
  keeping external Grafana static-key setup guarded and optional.
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

- Renamed SDLC-only workflow skills to `sdlc-*` names, with `start-sdlc`
  becoming `sdlc-start`, and made their front matter descriptions start with
  `Use only as part of the Agentic SDLC workflow;` so tool discovery separates
  SDLC phases from ordinary commands and general-purpose skills.
- Updated `align-skill` with an optional stateful-workflow profile for
  state-machine skills that manage local state, locked plans, evidence,
  continuation, retries, or failure routing, including a reusable template and
  `--profile stateful-workflow` validator support.
- Updated `create-pr` and `review-pr` with Agentic SDLC handoff guidance so
  they can read local SDLC evidence when invoked in that workflow while
  preserving their general PR behavior.
- Hardened `sdlc-start` so coordinator reruns resume from explicit
  checkpoints, repair partial pointer writes from the newest complete
  checkpoint, avoid duplicate history on unchanged resumes, and keep locked
  plans append-only by superseding instead of editing in place.
- Documented the runtime hook authorization handoff for Agentic SDLC:
  `sdlc-commit`, `create-pr`, and `sdlc-merge-pr` now write short-lived local
  authorization files under the active run before guarded Git actions, and
  `sdlc-start` state schema records the `permissions/` directory contract.
- Clarified hook ownership boundaries: non-SDLC global-context hooks own
  `SessionStart` and lightweight `UserPromptSubmit` context, while Agentic
  SDLC uses only separate `PreToolUse` and `Stop` hooks.
- Tightened global-context hook rules so `SessionStart` is limited to stable
  global context and `UserPromptSubmit` is limited to lightweight prompt-time
  hints, with no SDLC routing, requirements parsing, workflow skill selection,
  run-state creation, or large-document injection.
- Reduced global-context hook payloads so `SessionStart` and `UserPromptSubmit`
  no longer repeat detailed workflow or subagent lifecycle instructions already
  owned by `global-context-management`.
- Hardened `apply-security` validation guidance so lockfile maintenance,
  cache/build directory writes, external registry submissions,
  package-manager auto-install behavior, and live-credential checks require
  explicit scope and safety review.
- Updated `align-skill` so each alignment run captures evidence-backed,
  public-safe reusable learnings back into the target skill's local source
  materials before completion, and reports any source updates that were skipped.
- Updated `align-skill` so it can be triggered as a helper for hardening
  scaffolded or draft skills and applies safe, secure, fast authoring guidance
  without replacing `skill-creator` for initial scaffolding.
- Updated `align-skill` to recognize optional `evals/` folders for reusable
  trigger or quality-evaluation prompts, matching the existing `helmchart`
  trigger-eval convention, and added a validator self-test for the `evals/`
  acceptance plus unknown-folder warning behavior.
- Added a standard `## Learning Loop` rule to every reusable skill so durable,
  public-safe, evidence-backed learnings can be captured by the task skill that
  is already loaded, and taught `align-skill` validation to enforce the rule on
  target skills while preserving read-only and report-only task contracts.
- Tightened the `code-info` skill contract so invoking it is explicitly
  read-only information gathering: it reports from existing files only and does
  not edit, format, build, test, install, generate coverage, or stage files.
  Its helper also disables Git optional locks for Git inspection commands.
- Expanded `install-grafana-mcp-for-nebius` with a Nebius-managed Grafana
  token-refresh wrapper, Codex registration examples that launch the wrapper,
  and clearer tenant/project input guidance. The default path now avoids
  Nebius static-key and service-account setup unless the user explicitly asks
  to connect external Grafana to Nebius read endpoints.
- Hardened `install-grafana-mcp-for-nebius` for repeated laptop runs with an
  idempotent setup contract, check/apply local-config helper, managed shell
  defaults, check-before-add Codex MCP registration, and no duplicate
  static-key issuance unless rotation is explicitly requested.
- Aligned `install-grafana-mcp-for-nebius` with the current Grafana MCP docs
  and the latest released/Homebrew binary behavior: the wrapper now prefers
  token-file auth when supported, falls back to startup-only inline token auth
  for released binaries that lack token-file support, and the config helper now
  reports mismatched existing Codex MCP servers instead of silently reusing
  them.
- Minimized the global AGENTS template to durable always-on policy, moved
  detailed workflow guidance back to skills, preserved the explicit `$align`
  post-edit rule, and shortened high-impact skill descriptions so implicit
  routing front-loads trigger keywords.
- Tightened `config-codex` idempotency guidance so existing laptop
  `AGENTS.md` and `config.toml` files are inspected before backup or patching,
  template-only config settings are not backfilled on already working setups,
  and a read-only local preflight can verify that no changes are required.
- Clarified that `commit-push` always commits the whole Git repository from
  the Git root with `git add -A`, regardless of the service, package, chart, or
  project directory where the agent starts. Project-folder, current-directory,
  and pathspec-limited staging remain out of scope for this skill.
- Tightened `commit-push` so remote branch refresh uses the full
  `refs/heads/<branch>:refs/remotes/origin/<branch>` refspec and staged
  validation can repair small mechanical whitespace blockers, such as trailing
  whitespace or an extra blank line at EOF, before committing. Conflict markers,
  unresolved conflicts, broad formatter churn, semantic changes, dependency
  updates, and branch divergence still stop the workflow.
- Expanded the `nebius` skill's VPC networking guidance with the current
  Nebius network-pool/subnet model: explicit subnet CIDRs use
  `use_network_pools=false`, inherited subnets do not own every displayed
  status CIDR, subnet suggestions must account for explicit peer subnets and
  live allocations, existing-network out-of-parent CIDRs must extend an
  attached compatible private pool first, and new-network pool choices should
  hide assigned or empty private pools.
- Aligned `global-context-management` subagent guidance around the conservative
  `max_threads = 4` budget: use `repo_mapper` and `test_strategist` early only
  when useful, close helpers after consolidation, and reserve `risk_reviewer`
  for near-final review of non-trivial or risky changes.
- Clarified `global-context-management` subagent fallback guidance so, when
  delegation is already authorized and useful but controls are not visible,
  agents first use `tool_search` to look for deferred multi-agent/subagent tools
  before reporting delegation unavailable.
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
- Clarified `global-context-management` and `config-codex` delegation guidance
  so a prompt request and a user-enabled local hook policy request are both
  treated as valid subagent authorization sources, while hooks still only
  inject model-visible context and do not directly call subagent tools.
- Clarified the read-only subagent lifecycle: the parent agent should ask
  helpers for concise final summaries, wait for results, consolidate them, and
  close completed subagent threads when close controls are available and no
  follow-up is needed. When multiple helpers are running, the parent should
  close each completed handle as its terminal result arrives and continue
  waiting on the remaining handles.
- Documented the parent/subagent mental model in the `config-codex` and
  `global-context-management` READMEs.
- Clarified that direct live hook probes with synthetic `session_id` values can
  leave scaffold-only task-state directories, and removed the no-`session_id`
  manual fallback path from the task-state hook contract.
- Extended `global-context-management` local template validation to prove an
  existing nonempty `current.md` task-state file is preserved for the agent to
  read instead of being overwritten or injected into hook context, and tightened
  hook templates so reused task-state files are chmod-repaired to `0600`.
- Changed task-state hook behavior to advertise-only: `SessionStart` injects
  the session-scoped path without creating a missing `current.md`, while
  `UserPromptSubmit` only advertises or reuses the same path for complex
  prompts and does not create task-state, SDLC run state, or workflow state.
- Tightened the `global-context-management` `SKILL.md` non-goals so a missing
  global task-state path no longer permits repo-local or manual fallback state;
  repo-local task-state files remain explicit-user-request only.
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
- Tightened the `create-pr` current-feature-branch path so invoking it from a
  non-default branch reuses that branch, stages existing work with
  `git add -A`, validates the staged diff, commits before switching or
  merging, pushes the branch, and opens or reuses the PR without creating
  another branch.
- Updated the `create-pr` guidance and metadata so PR commits always stage
  current dirty work from the repository root with `git add -A` instead of
  narrowing commits to selected paths.
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
  monorepo diff with `git add -A` before committing, and stops instead of
  path-limiting the commit when repo-wide staging would include work that
  should not be part of the PR.
