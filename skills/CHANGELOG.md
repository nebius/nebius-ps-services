# Changelog

All notable changes to the reusable Codex skills are tracked here.

## [Unreleased]

### Added

- Added hook file provenance to `install-skills.sh` hook payload sync so
  source and target hashes are recorded, and differing existing hook payloads
  are backed up before being refreshed under
  `$CODEX_HOME/.install-hooks-state/backups/`.
- Added the `agent-nebius-auth` setup-only skill for bootstrapping and
  repairing Codex Agent Nebius service-account authentication, including an
  idempotent setup script and a `PreToolUse` hook that injects short-lived
  Nebius token environment variables into matching Bash commands without
  returning token material as model context, fail-closed token disclosure
  guardrails, and local hook regression tests.
- Added `docs/agentic-sdlc-design.md` to document the Agentic SDLC
  architecture, requirements/design templates, local run state, hook
  boundaries, MCP role, and skill-by-skill lifecycle.
- Added the Agentic SDLC skill set: `sdlc-create-requirements`, `sdlc-start`,
  `sdlc-gather-context`, `sdlc-create-design`, `sdlc-create-plan`,
  `sdlc-tdd`, `sdlc-implement-plan`, `sdlc-validate-codes`,
  `sdlc-unit-tests`, `sdlc-evaluate`, `sdlc-classify-failure`,
  `sdlc-auto-steering`, `sdlc-gui-test`, `sdlc-tui-test`,
  `sdlc-update-documents`, `sdlc-commit`, `sdlc-uat-tests`,
  `sdlc-merge-pr`, and `sdlc-align-specs`. These skills keep committed
  product truth in `docs/requirements.md` and `docs/design.md`, keep execution
  state under `~/.codex/sdlc-runs`, record mid-run prompts in private steering
  state, update project-facing docs from implemented evidence, and model the
  SDLC loop as skill-selected phases rather than a workflow CLI. The SDLC flow
  reuses existing `create-pr` and `review-pr` as PR creation and review
  handoff phases.
- Added SDLC templates for requirements, design, context packs, locked feature
  plans, validation/test/evaluation evidence, local commit evidence, and UAT
  reports, plus state-schema and failure-taxonomy references.
- Added a source bundle for optional Agentic SDLC PreToolUse and Stop hooks
  under `sdlc-start/assets/hooks/`, including shared hook helpers and local
  unit tests for continuation, write-policy, authorization, steering, and
  short-alias behavior.
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
- Added the `brainstorm` Codex skill for evidence-first, chat-only ideation
  before implementation, with source priority across the current project
  folder, sibling repo folders, related skills, internal Confluence/Slack/Jira
  sources when available, and official vendor docs.
- Added the `create-learning-course` Codex skill for creating public-safe
  course workspaces, syllabi, lessons, exercises, glossaries, learning records,
  reusable assets, and publication review checkpoints from a learner mission
  and trusted sources, using a mission-led learning model inspired by the
  public teach-skill pattern with explicit redaction and
  sensitive-information guardrails.
- Added the `research` Codex skill for senior-engineer technical due
  diligence on technologies, APIs, RFCs, protocols, architecture patterns,
  products, and feature requirements, with ranked sources, internals,
  operations, limitations, alternatives, and actionable recommendations.
- Added the `design` Codex skill for general software design before
  implementation, covering brownfield and greenfield paths, requirement
  understanding, code/docs inspection, `research`-backed topic and technology
  due diligence, component and architecture design, alternatives evaluation,
  and Codex `/plan` handoff.
- Added the `code-review` Codex skill for strict implementation-quality audits
  of the current branch, local diff, changed files, or provided patches,
  focused on maintainability, abstraction quality, modularity, type boundaries,
  file-size growth, spaghetti branches, and missed structural simplifications.
- Added the `system-design-rules` Codex skill for applying a refined 100-rule
  software system design checklist to architecture proposals, ADRs, design
  docs, API and data model choices, reliability, security, observability,
  performance, cost, migration, and team-ownership decisions before
  implementation.
- Added the `task-implementer` Codex skill for narrow brownfield implementation
  loops that order prompt-derived work as `task-1` through `task-n`, keep one
  write task active at a time, and hand off between fresh Codex sessions
  through private markdown checkpoints.
- Added the `agentic-sdlc-test` Codex skill for safely verifying the Agentic
  SDLC workflow from outside the workflow with global `sdlc-*` skill discovery,
  hook configuration inspection, disposable PreToolUse and Stop hook fixture
  tests, disposable golden-path guidance, idempotency and failure-loop checks,
  and a report under `~/.codex/sdlc-verification/`.
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
- Added the `commit` Codex skill for fast local commits on the current branch:
  it stages the complete repository diff with repo-root `git add -A`, runs
  lightweight staged validation, commits with normal hooks, and intentionally
  does not push, create PRs, or participate in Agentic SDLC state.
- Added the `code-info` Codex skill for copy/paste-friendly project code
  metrics, including LOC by language and component, repo size and link,
  test-file counts, CLI command detection, module/package counts, artifact
  sizes, and available coverage artifacts. It supports local folders and
  remote GitHub repositories that are not cloned locally by reading a temporary
  repository archive with local GitHub token discovery when needed.
- Added the `create-pr` Codex skill for branch-safe GitHub PR creation.
- Added the `merge-pr` Codex skill for non-SDLC GitHub PR merging that verifies
  PR metadata, checks, reviews, mergeability, base branch, and head SHA before
  calling `gh pr merge --match-head-commit` without admin bypass.
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
- Added the explicit-only `nebius-audit-log` Codex skill for bounded,
  read-only Nebius Control Plane Audit Logs queries by resource or current
  subject, with a sanitized CLI helper, official Nebius reference notes, and
  fake-CLI unit tests.
- Added skill-local `README.md` files across reusable skills so each skill
  folder has human-facing architecture, workflow, core concept, and file
  responsibility documentation.
- Added the `publish-helm` Codex skill for OCI Helm chart publishing.
- Added the `review-pr` Codex skill for GitHub-backed PR review and
  merge-readiness work.

### Removed

- Removed the `onboard-nebius-cxcli` and `release-generator` skills.

### Changed

- Deduped and shortened `global-context-management/SKILL.md` so the runtime
  skill load keeps one canonical task-state section and one canonical
  delegation/lifecycle section while preserving the existing validators and
  subagent authorization contract.
- Relaxed `global-context-management` read-only subagent delegation wording so
  a user-enabled local hook policy request is treated as sufficient current-turn
  authorization when the runtime and active instructions permit delegation. The
  parent agent should dynamically spawn useful targeted read-only helpers
  without asking for another user prompt only because the original prompt did
  not mention subagents.
- Changed hook payload installation so selected source bundles refresh
  differing existing hook files by default after backup, while hook
  registration is preflighted before payload sync and still preserves existing
  `hooks.json` entries unless `--replace-hooks-json` is explicitly set.
- Aligned `publish-helm`, `publish-image`, and `publish-release` project-local
  shell helper templates with their canonical doer scripts: generated helpers
  now use `--mode prep|publish|verify`, require explicit `--tag`, reject the
  old `--prep`/`--publish` interface, keep setup defaults overridable by flags,
  include artifact-specific verify modes, and check duplicate release tags before
  the branch-sync fetch can alter local tag refs.
- Hardened `publish-release`, `publish-image`, and `publish-helm` so release
  prep and publish phases must start from a clean, synced default branch; prep
  now creates and pushes `release/<tag>` from that branch, and the old
  non-default-branch publish escape hatch fails fast.
- Refreshed `align-skill` against current official OpenAI Codex skill guidance,
  separating the portable `SKILL.md` minimum from this repository's stricter
  source-owned skill structure, clarifying optional upstream
  `agents/openai.yaml` versus required repo policy, documenting optional
  `references/`, `scripts/`, `assets/`, and repo-local `evals/` surfaces, and
  updating the OpenAI metadata template. The repo standard preserves
  `agents/openai.yaml` for every source-owned skill so invocation policy and
  interface metadata stay reviewable.
- Updated `align-skill` so every target skill alignment must apply
  `code-review` in review-only mode and `apply-security` in advisory or scan
  mode before claiming the target is aligned, with report templates recording
  fixed, deferred, skipped, incomplete, or blocking review-lane findings.
- Tightened `brainstorm` source gathering so source-ranked context must be
  relevant to the exact topic, question, or problem statement and limited to
  information that can answer the question, resolve the challenge, close a
  named gap, or change the recommendation.
- Hardened `config-codex` laptop setup so existing `AGENTS.md` and
  `config.toml` are treated as merge targets by default, exact template checks
  are explicit audit modes, default validation rejects empty or stale managed
  `AGENTS.md` blocks, and reviewed hook payload sync backs up differing
  existing hook files before refreshing them from source.
- Enhanced `align` into a changed-scope post-change quality gate that
  coordinates mandatory cross-code validation, `code-review`, `linter`,
  `apply-security`, and focused test/build lanes while keeping safe-only
  remediation, incremental scope expansion, and explicit approval for risky
  security or public-contract changes.
- Expanded `code-review` from strict structural implementation-quality review
  into a neutral, evidence-based findings-first review skill that also covers
  bugs, regressions, meaningful test gaps, reliability and operations risk,
  security-adjacent implementation issues, owner-review needs, and severity
  decisions while preserving boundaries with `review-pr`, `align`,
  `apply-security`, and `system-design-rules`.
- Hardened `task-implementer` into an explicit-only sequential implementation
  loop where each task session validates the active task, runs `code-review`,
  fixes scoped findings, commits through `$commit`, records review and commit
  evidence in the private handoff, stops, and hands the markdown context to the
  next fresh Codex session.
- Changed `apply-security` to implicit invocation so it can act as a general
  security adviser during design, implementation, review, and validation
  sessions, while keeping patching constrained to authorized low-risk
  remediations.
- Clarified that `align` coordinates the `apply-security` security lane by
  resolving and reading the `apply-security/SKILL.md` contract directly when it
  is absent from the initial skills list because of budget, installation, or
  discovery limits.
- Refined `align` wording so its child-skill coordination, mandatory
  `apply-security` lane, and high-risk security approval boundaries are clearer
  and less repetitive.
- Clarified that `align` separates active changed scope from unrelated dirty
  worktree files and reports unrelated changes instead of silently treating
  them as alignment scope.
- Extended `agent-nebius-auth` runtime injection for long-running Bash commands
  by exporting `NEBIUS_AUTH_CREDENTIALS_FILE`, defining a
  `nebius_refresh_token` Bash helper through a restricted temporary `BASH_ENV`
  file for child Bash processes, denying common nested environment dumps, and
  documenting the credential-file or per-operation refresh pattern for raw
  Nebius API calls.
- Fixed `nebius_refresh_token` so every refresh clears existing token
  environment variables before invoking the Nebius CLI profile token command,
  preventing later refreshes from reusing a stale `NEBIUS_IAM_TOKEN`.
- Hardened `install-grafana-mcp-for-nebius` local config checks so an existing
  Codex MCP server that points at a byte-identical source or installed wrapper
  copy is treated as matching state instead of a replacement-required
  mismatch.
- Hardened `install-grafana-mcp-for-nebius` token safety so the Nebius wrapper
  refuses external Grafana URLs, config checks redact token-like MCP fields,
  arbitrary byte-identical wrapper copies are not trusted, shell startup scans
  block persisted token exports, and token refresh intervals must stay below
  the Nebius token lifetime.
- Aligned `install-grafana-mcp-for-nebius` metadata and trusted source-wrapper
  checks so project/resource query prompts use PromQL-compatible datasource
  wording and source checkouts are recognized by skill-relative path rather
  than a user-specific repository path.
- Expanded `install-grafana-mcp-for-nebius` usage docs with a bounded
  datasource-discovery, label-discovery, and project/resource metric-query
  workflow for Nebius-managed Grafana.
- Simplified `install-grafana-mcp-for-nebius` usage prompts around
  user-facing Grafana questions, added a redacted MK8s GPU usage example, and
  documented the Codex-to-MCP-to-Grafana query flow.
- Updated `brainstorm` so major design or architecture decisions consult the
  installed `design` and `system-design-rules` skills as advisory context when
  accessible while keeping brainstorming chat-only until the user asks to
  switch workflows.
- Set `create-learning-course` to explicit-only invocation because course
  creation can create or revise many local files.
- Tightened the `design` skill so topic, feature-requirement, architecture,
  product, standard, and technology due diligence routes through `research`
  when that skill is available, leaving `design` focused on synthesis,
  alternatives, and `/plan` handoff.
- Extended the `design` skill so standard, deep, architecture-heavy, or
  hard-to-reverse solution designs apply `system-design-rules` as an advisory
  checklist before final `/plan` handoff while leaving checklist-only reviews
  owned by `system-design-rules`.
- Strengthened `sdlc-create-design` with the same phased design discipline used
  by `design` while preserving Agentic SDLC ownership of `docs/design.md`: it
  now records existing-system evidence, selected and rejected options,
  implementation boundaries, validation/test/evaluation plans, rollout,
  rollback, and context-gap routing before a feature can move to planning.
- Strengthened `sdlc-validate-codes` so mechanical build/lint/type/import,
  dependency, and configuration checks are followed by `code-review` in
  review-only mode; blocking review findings now keep the feature out of the
  `validated` state and route repair to the responsible SDLC phase.
- Extended the Agentic SDLC requirements, start, evaluation, and UAT skills
  with an optional Live Experiment Environment contract so users can provide a
  confirmed non-production or disposable target, safe connection references,
  allowed actions, reset instructions, approvals, and evidence limits for later
  agent experiments without storing secret values.
- Updated the `research` skill source order so organization-tied research
  searches internal Slack and Confluence sources through available connectors
  first, uses MCP or app-tool access for internal systems when connectors are
  unavailable, verifies technical claims with official vendor or authoritative
  external sources, and labels organization-specific guidance separately from
  vendor facts and industry practice.
- Simplified `install-skills.sh --install-all-hooks --register-hooks` output
  into a single per-source `Hooks status` report and only shows the restart and
  trust warning when hook files or registrations changed.
- Simplified `agent-nebius-auth` setup inputs so operators pass `--tenant-id`
  plus exactly one project selector, either `--project-id` or
  `--project-name`; the setup script now resolves the missing project metadata
  through Nebius project lookup and fails fast when both selectors are passed.
- Aligned the `config-codex` global `AGENTS.md` template with the compact live
  Codex policy and synced mirrored prompt-hook delegation wording across
  `config-codex` and `global-context-management` validation.
- Added bounded same-workspace prior task-state candidate discovery to the
  `global-context-management` prompt hook and mirrored `config-codex` hook
  template, exposing candidate paths only while keeping historical task-state
  contents out of model context and preserving the current session
  `current.md` as the write target.
- Hardened `global-context-management` and mirrored `config-codex` guidance so
  parent agents must close every spawned subagent handle that is completed or
  no longer needed before finalizing when close controls are available, and
  must report any unavailable or failed cleanup instead of leaving helper
  sessions open silently.
- Strengthened `global-context-management` and `config-codex` task-state
  guidance so `$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md`
  is documented, templated, and hook-advertised as a compact rolling summary
  rather than an append-only transcript.
- Reworked the root `README.md` with a short table of contents and a complete
  grouped skill catalog covering every live source skill, its invocation
  policy, and a concise description.
- Hardened `align-skill` authoring guidance and validator coverage for current
  OpenAI Codex skill metadata: `agents/openai.yaml` is documented as optional
  upstream but required by this repository's source-skill convention, the
  OpenAI metadata template now includes invocation policy, and validator tests
  cover wrong metadata paths plus explicit-only policy derived from `SKILL.md`.
- Added explicit `agents/openai.yaml` invocation policies across all source
  skills: operational setup, commit, PR, merge, publish, security, code-info,
  container, Grafana MCP, `agentic-sdlc-test`, and every `sdlc-*` workflow
  skill now disable implicit invocation, while the remaining non-SDLC skills
  opt in to implicit matching. The Agentic SDLC Stop hook now emits explicit
  `$sdlc-start` continuation prompts, and the skill-structure validator checks
  invocation policy drift.
- Updated the `config-codex` global `AGENTS.md` template to include compact Git
  workflow defaults, explicit implicit-skill invocation policy, narrower
  mutating skill-script permission wording, and a broader changed-surface
  `$align` rule for code, config, and documentation changes.
- Hardened `agent-nebius-auth` setup idempotency so Nebius profile
  create/update restores the previously active human/admin CLI profile, setup
  serializes global profile mutations, agent service-account profiles are not
  treated as human/admin IAM repair sessions, and fake Nebius CLI regressions
  cover profile drift on rerun, effective `NEBIUS_PROFILE` selection,
  interrupted profile creation, and concurrent setup.
- Changed `agent-nebius-auth` so the setup script no longer writes an inline
  Codex hook block to `$CODEX_HOME/config.toml`. The unsupported
  `--install-hook` flag now fails fast and points operators to the canonical
  `install-skills.sh --install-hooks agent-nebius-auth/assets/hooks
  --register-hooks` path for payload sync and `hooks.json` registration, while
  setup records the selected project under `~/.nebius` for the generic hook to
  read locally.
- Removed the installer-side `agent-nebius-auth` inline `config.toml`
  migration cleanup. The canonical path is now fail-fast setup plus root
  installer hook sync/`hooks.json` registration only; stale inline hook entries
  are rejected before hook files or `hooks.json` are changed and must be removed
  manually instead of being migrated.
- Added a skill-local `agent-nebius-auth/README.md` with the setup-only
  workflow, installer-managed hook registration, runtime hook behavior,
  idempotency checks, and validation commands.
- Clarified `publish-helm` required release inputs so callers must provide an
  explicit tag and publish destination or be asked for them, documented the
  OCI repository base versus provider registry-ID workflow split, and made
  verify mode fail fast without requiring local chart files.
- Realigned the `publish-helm` setup helper template with the maintained
  chart-local helper behavior for prerelease tags, branch pushes, dependency
  validation, fallback changelog notes, and explicit missing-input diagnostics.
  Generated helpers now show help and argument errors before Helm/Python
  readiness checks.
- Added an explicit `install-skills.sh --replace-hooks-json` opt-in for hook
  registration cleanup so normal `--register-hooks` preserves hand-written
  entries while the intentional clean-reset mode backs up and replaces
  `$CODEX_HOME/hooks.json` from the selected source manifest or manifests.
- Added a hook-registration guardrail that refuses multiple registrations for
  the same hook event and Python hook filename, preventing stale entries such
  as duplicate `Stop` hooks for `stop_sdlc_continue.py` from being merged.
- Standardized the `agent-nebius-auth` hook payload under
  `agent-nebius-auth/assets/hooks/` so `--install-all-hooks` discovers and
  registers it with the other reviewed hook bundles.
- Relaxed the Agentic SDLC PreToolUse write-target policy so file targets are
  no longer blocked by path, including outside-repo files, credential
  directories, Codex runtime files, global `AGENTS.md`, locked SDLC plans, and
  private SDLC state. Ordinary outbound network commands are allowed while
  secret-bearing payloads, dangerous shell patterns, and guarded Git/GitHub
  actions remain blocked.
- Packaged the Agentic SDLC hook helper library with `lib/__init__.py` and
  explicit `lib.*` imports so direct script execution and Pyright/Pylance
  import resolution stay aligned.
- Refactored `publish-helm`, `publish-image`, and `publish-release` from
  generator-first guidance into doer-first release skills that can run setup,
  prep, publish, or complete end-to-end flows from the current project folder.
- Added deterministic skill-owned publish helper scripts for Helm charts,
  container images, and GitHub Releases, including strict clean-worktree checks,
  tag normalization, duplicate tag prevention, changelog release-section
  validation, upstream push setup, and artifact-specific publish verification.
- Hardened the skill-owned publish helpers so rerunning prep for an existing
  unreleased tag section merges new `Unreleased` bullets into that section
  instead of clearing them.
- Hardened publish helper scripts and setup templates to reject prep on the
  default branch and push feature branches explicitly to `origin`.
- Updated Helm chart prep to include generated dependency lock/archive outputs
  in the release-prep commit when `helm dependency update` creates them.
- Aligned the Helm setup helper template with the canonical doer so generated
  project-local helpers build or update chart dependencies before linting.
- Clarified `publish-helm` chart-version ownership so `Chart.yaml` updates are
  release-prep changes that must be merged before the publish/tag phase, with
  publish helpers now pointing mismatched versions back to prep.
- Fixed Helm release prep to avoid passing ignored generated chart archive
  directories to `git commit` when no dependency archives are staged.
- Added image publish workflow tag ancestry validation and removed shallow
  history from release publish ancestry checks.
- Aligned `merge-pr` with GitHub merge-queue behavior by using the no-strategy
  `gh pr merge --match-head-commit` path for merge-queue branches instead of
  treating a ready queue as an admin-bypass case.
- Expanded `docs/agentic-sdlc-design.md` with an explicit SDLC workflow
  verification procedure, including quick preflight usage, full disposable
  golden-path testing, report status interpretation, and fix/rerun policy for
  `agentic-sdlc-test`.
- Hardened `agentic-sdlc-test` hook fixture execution so subprocesses disable
  Python bytecode writes and do not create `__pycache__` artifacts in skill
  source folders.
- Aligned `global-context-management` and `config-codex` local setup guidance
  to syntax-check rendered hooks without bytecode writes, and clarified that
  installable global-context hook bundle sync is owned by `config-codex`.
- Strengthened `agentic-sdlc-test` design-contract preflight so it verifies the
  Agentic SDLC design document includes the workflow verification procedure and
  report path, not only the core SDLC lifecycle terms.
- Updated `align` so alignment decisions first synthesize the active thread,
  relevant Agent Memory, and durable task-state or workflow state files, while
  treating those sources as leads that must be verified against current
  repository or runtime evidence before safe fixes.
- Clarified `sdlc-commit` boundaries so ordinary local commits use `commit`
  and publish commits use `commit-push`; `sdlc-commit` remains Agentic
  SDLC-only.
- Tightened `create-pr` so dirty PR work runs safe formatting, whitespace,
  lint, build, and focused test checks before `git add -A` and commit, waits
  for local tests to finish, and uses `git fetch origin` plus
  `git merge --no-edit origin/<base>` before PR creation so branches receive
  the latest base updates without rewriting history. Pushing now uses explicit
  refspecs, and available GitHub checks must reach a terminal state before the
  PR is reported ready.
- Relaxed `global-context-management` subagent guidance so, after a prompt or
  user-enabled local hook policy authorizes delegation, Codex should choose
  useful targeted read-only helper roles instead of waiting for the prompt to
  name them, while still treating runtime tool availability and active
  instructions as hard gates; `SKILL.md` now also has explicit stateful
  workflow sections for inputs, required reads, writes, idempotency, failure
  handling, must-not rules, process, and completion criteria.
- Aligned optional hook-assisted subagent delegation so the opt-in policy
  template is enabled, hook context explicitly asks the parent Codex agent to
  dynamically spawn useful read-only helpers, the local idempotency preflight
  flags present-but-disabled policy files, and docs keep hook-direct tool calls
  out of scope.
- Updated `config-codex` output guidance so local setup runs report aligned,
  not-aligned, and locally blocked surfaces explicitly, including exact manual
  out-of-band remediation steps when a PreToolUse or permission guard blocks a
  safe patch, and added disposable fixture coverage for local idempotency
  policy-file edge cases.
- Hardened the Agentic SDLC verifier path resolution so installed skill copies
  can locate the repo design doc when checking the current checkout.
- Renamed SDLC-only workflow skills and the coordinator to `sdlc-*` names, and
  made their front matter descriptions start with
  `Use only as part of the Agentic SDLC workflow;` so tool discovery separates
  SDLC phases from ordinary commands and general-purpose skills.
- Hardened the Agentic SDLC Stop hook source so continuation prompts emit
  `sdlc-start` and canonical `sdlc-*` next-skill names, normalize short
  phase aliases only as input, and recognize pause or PR-control steering such as
  `Pause after the current feature. Do not create a PR.`
- Allowed the Agentic SDLC PreToolUse hook source to write
  `$CODEX_HOME/task-state` files so `global-context-management` can persist
  session-scoped continuity notes while unrelated `$CODEX_HOME/hooks` writes
  remain blocked.
- Updated `install-skills.sh` with an explicit
  `--install-hooks <source_hook_dir>` option that copies hook files from a
  selected hook source directory into `${CODEX_HOME:-$HOME/.codex}/hooks`,
  keeping runtime hook sync separate from normal skill installation and from
  `hooks.json` trust decisions.
- Added `install-skills.sh --install-all-hooks` to install every reviewed
  skill-owned hook bundle from `*/assets/hooks` under the source skills folder,
  with destination collision checks and idempotent unchanged reporting for hook
  files.
- Extended `install-skills.sh` hook installation with an explicit
  `--register-hooks` option that semantically merges source `hooks.json` or
  `hooks.json.template` registrations into `$CODEX_HOME/hooks.json`, preserving
  existing hook entries and backing up the file before changes.
- Added an idempotent, report-only hook drift summary to `install-skills.sh`
  that lists extra installed hook files and `hooks.json` registrations not
  present in the selected source manifests, with manual cleanup guidance
  instead of automatic deletion.
- Aligned `config-codex` idempotency checks with the current local setup model:
  exact global `AGENTS.md` parity when requested, required global hook
  registrations as a `hooks.json` subset, and required public-safe MCP servers
  while preserving extra workflow hooks and machine-specific MCP entries.
- Strengthened `global-context-management` wording so authorized read-only
  subagents are preferred for useful read-heavy sidecar work when the prompt or
  local hook policy authorizes delegation and the current runtime permits it.
- Updated `align-skill` with an optional stateful-workflow profile for
  state-machine skills that manage local state, locked plans, evidence,
  continuation, retries, or failure routing, including a reusable template and
  `--profile stateful-workflow` validator support.
- Clarified the `align-skill` README definition of stateful workflow skills,
  including when to use the profile and a concise SDLC coordinator example.
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
  activation make subagent delegation possible but do not directly spawn
  helpers. Current runtime probes can ask Codex to use or spawn subagents, use
  delegation, or run parallel agents, or rely on a user-enabled local hook
  policy that injects a bounded read-only delegation request; the `codex exec`
  examples now use the current `--sandbox read-only` flag instead of the
  obsolete `--ask-for-approval` option.
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
  close spawned subagent handles when close controls are available once they
  are completed or no longer needed. When multiple helpers are running, the
  parent should close each completed handle as its terminal result arrives,
  continue waiting on the remaining handles, and report any unavailable or
  failed cleanup before finalizing.
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
