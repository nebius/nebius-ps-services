# Agent Portability Alignment

Source review date: 2026-09-12. Scope: all 71 skills and seven hook payload
owners in this catalog. Existing working changes were the preservation baseline;
no existing source/resource path or native metadata file was removed.

## Result and boundaries

All skills now declare native invocation and Help forms, preserve their
invocation policy, and distinguish the executing host from any configured
product. Shared workflows use native private homes, identity and hook adapters.
The source is installable for Codex and Claude; runtime activation and
comparative model quality remain **UNAVAILABLE** because isolated native CLI
probes lacked authentication. Real user installations, live infrastructure,
Docker applications and GUI UAT were not exercised by this alignment.

Agent Skills standard fields pass for every skill. The catalog intentionally
retains native policy extensions: 25 public explicit-only skills use Claude
`disable-model-invocation`, and 19 internal phases use `user-invocable: false`
plus coordinator checks. All 71 Codex `agents/openai.yaml` files are unchanged.
Strict consumers that reject all host extensions may reject those 44 files;
removing the controls would weaken supported behavior. The remaining 27 files
also pass strict standard-only frontmatter checks.

## Preservation and changes

The before-edit inventory recorded each skill's purpose, public interface,
outputs, headings, metadata, resources and canonical evals. The table below
links each retained contract and records the deterministic before/after counts.
All 791 original trigger cases remain unchanged; three host-routing cases per
skill bring the total to 1,004. Every target retains Help and Learning Loop.
The same named public workflows and their authorization, state, retry,
idempotency, recovery and output contracts remain required on both hosts.

| Change group | Scope and preserved behavior |
| --- | --- |
| Invocation | All skills: `$name`, `/name` and native plugin `/skills:name`; Help remains tool-free after skill loading. Missing tools do not grant weaker execution. |
| Shared runtime | GCM, prompt intake, Task Implementer, Commit and SDLC use native homes/session sources. Protocol field names, native worker identity, commit claims and one Stop arbiter remain intact. |
| Private CLI | Shared home options use `--agent-home`; PAI also requires `--agent`, and worker dispatch uses `--agent-binary`. No old option aliases were introduced. Existing serialized Codex fields remain stable. |
| Worker execution | SDLC uses native print-mode structured output for Claude; its envelope, lease, liveness and process cleanup checks remain enforced. Task Implementer uses the selected native CLI with exact start/recovery and immutable-result checks. Both use the selected private home, working directory and fresh identity. |
| Test lifecycles | Existing Codex `codex-home` state remains valid; Claude uses `claude-home`. Cross-host trial reuse fails before mutation. Existing Codex context schema is preserved; Claude has a distinct schema. |
| Project instructions | Claude declares native instruction/settings/import digests bound to its current hook session and an existing tracked CLAUDE.md import. The AGENTS.md target, exact approval, private ownership, managed tail and recovery remain unchanged. Private local instructions remain conflict context, not published content. |
| Configured products | `config-codex`, `config-claude` and Codex Grafana MCP setup retain their intentional product targets regardless of the executing agent. |
| Hook safety | Reject writable runtime ancestry/counters and linked or writable payloads. Preserve source caches, unrelated settings, registration refresh, native tool handling and continuation limits. |

## Validation evidence

| Lane | Result |
| --- | --- |
| Repository profiles | All 71 skills pass Codex and Claude with required canonical evals; six stateful workflow owners also pass the stateful profile on both hosts. |
| Portable core | All 71 standard-field checks pass; native extensions are disclosed above. |
| Validator regressions | Structure 35, frontmatter 16, npx checker 11 and evaluation adapter 10 tests pass. |
| Installed packages | Cross-agent installation/runtime suite: 27 tests pass, including full catalogs, repeat installation, settings preservation, hook entrypoints, source parity, native identity and negative trust checks. |
| npx distribution | Actual pinned `skills@1.5.26`: all 71 discovered and copied for `codex` and `claude-code`; payload parity, repeated installation and isolation pass. This route does not register hooks. |
| Workflow regression | PAI 92 plus retention 8; SDLC verifier 85, execution 79, worker dispatch 13 and native lifecycle 60; Task Implementer lifecycle 39; Commit transaction 22; SDLC hooks 111 and project hooks 38 pass. |
| Additional regression | Task Implementer prompt workspace 90 and interoperability 13; SDLC prompt workspace 45; prompt intake 27, hook 19 and contract 5; troubleshooting hook 97, helpers 28, contract 16 and preflight 8 pass. |
| Configuration | Claude roles 8 and configuration 25; Codex configuration 70; shared local-template validation pass. |
| Native triggers | Codex CLI 0.154.0 and Claude Code 2.1.236: UNAVAILABLE, authentication absent in isolated homes. |
| Comparative quality | Same baseline/candidate sample on both native CLIs: UNAVAILABLE for authentication. No quality or native activation pass is claimed. |
| Quality gates | Scoped code review, security review, Python syntax/Ruff, Markdown, Bash syntax, native manifests and canonical specification validation pass. |

The native plugin namespace was initially used in a standalone Claude eval
fixture. That fixture was corrected to `/name` and replayed; the corrected
probe reports authentication unavailable. An installation trial whose source
changed during repeated installation was excluded and replayed from a frozen
candidate. Neither discarded trial supports the results above. Remote CI was
not executed during this task.

## Review findings and resolution

The instruction review covered every selected SKILL.md delta and retained
metadata/resources. Support-code/security review focused on changed execution,
identity, trust, discovery and verification boundaries. It did not re-certify
unchanged vendor integrations against live services.

- Unsafe runtime/cache permissions and hardlinks: rejected at the shared
  boundary, with both-host negative regressions; writable counters stay intact
  when rejected.
- Claude discovery: fixed independent body capacity, private local-source
  classification, inline and target imports, and Markdown code/comment
  boundaries. Tests exercise missing/stale sources and actual managed create,
  verify and approved retirement while preserving the native import bridge.
- Existing Codex state: retained original shapes and protocol keys rather than
  requiring a new host field; Claude receives distinct native declarations.
- Stale verifier/test assumptions: updated canonical spec ownership, v2
  templates, native CLI options and required conditional-reference checks.
  Restored missing Task Implementer capture documentation; its seven contract
  mismatches also reproduced against the original working-byte baseline.
- Final bounded read-only review found no remaining serious issue in these
  changes. Completed helper agents had no close control available.

No unrelated changes were reset or staged. No credentials, private hostnames,
user configuration or internal source material were copied into the report.
Durable learning is captured in the executing-agent and Claude-discovery
references and their regression tests. Task-owned temporary baselines, frozen
candidates, evaluation reports and newly written bytecode caches were removed
after comparison; pre-existing unrelated caches were retained.

## Follow-up project alignment

A subsequent explicit `$align` reviewed entrypoint wiring, native worker
execution, CI coverage and connected documentation. It found three gaps:

| Finding | Resolution and evidence |
| --- | --- |
| ALIGN-WORKER-001 | Task Implementer’s sequential and recovery fallback still launched Codex for Claude sessions and inherited the parent identity. A failing routing/environment test reproduced both defects. The helper now selects the native CLI, sets the selected home/cwd and clears parent identities through the shared runtime adapter. Original Codex arguments, exact start/recovery prompts, lock release and immutable-result requirements remain intact. Claude uses print mode and native configured permissions without a bypass. Private launch names are host neutral with no old aliases. |
| ALIGN-CI-001 | Five native workflow harness suites were absent from CI. The existing skills verification job now runs them; YAML inspection and actionlint validate the wiring. These suites use local fixtures and do not prove Docker, browser or model behavior. |
| ALIGN-DOC-001 | Remaining shared-workflow README state paths, session names and fallback descriptions assumed Codex. Root and Task Implementer documentation, skill instructions and the handoff template now describe the selected host. Intentional configured products retain their native names. |

Task Implementer regressions exercise both hosts’ command arguments and recovery
prompts, real fixture subprocess environment/cwd/result creation, unavailable
executables, child failure and missing immutable results. Fixture processes do
not exercise a native model. The first subprocess fixture incorrectly combined
Claude selection with an active Codex thread identity; the fixture was corrected
to represent a Claude session, preserving the runtime’s native-host precedence.

The request restores the already documented REQ-030/FEAT-029 behavior. It adds
no requirement or design contract; canonical specifications validate unchanged.
The existing design links this report for current implementation evidence.
A bounded read-only review found no blocking issue in the worker repair.
Authenticated native execution and remote CI remain unverified. Installed
Claude 2.1.236 help supports the chosen flags; current
[Claude environment documentation](https://code.claude.com/docs/en/env-vars)
supports nested noninteractive print sessions, so no additional native marker
variables were removed.

Fresh follow-up checks pass: all 71 skills on Codex, Claude and portable-core
profiles; native manifests and canonical specs; 27 isolated installation/runtime
tests; and actual pinned `skills@1.5.26` discovery, resource/mode parity, repeated
copying and isolation for both hosts. Task Implementer resume tests (52), prompt
workspace tests (90), execution tests (10), dependency-wave tests (65) and
contract smoke pass. The five
native harness suites now wired into CI total 196 passing tests. Scoped Ruff,
Markdown, actionlint, workflow shell syntax and diff whitespace checks pass.
The installed skill payloads matched a frozen source candidate. Only this
non-installed evidence report was updated after those installation checks.
Only eight catalog files and the existing skills CI workflow changed in this
follow-up. No source paths were removed or executable modes changed; unrelated
working changes remain.

## Progressive disclosure

`sdlc-workflow-test` rose from 499 baseline lines to 513 during host adaptation.
Its explicit live-mode ten-step procedure was classified as conditional
knowledge and moved intact to `references/three-tier-process.md`, with a direct
required read before live operations. Mode parsing, authority, failure,
idempotency, cleanup, completion and output boundaries remain in SKILL.md.
The final main file has 425 lines. Reference content was compared byte-for-byte
before normalizing only trailing blank lines; the computer-use contract checks
also validate the new required-read route.

`troubleshoot` remains an explicit soft-budget exception: 614 baseline lines,
623 current lines. Its retained blocks comprise the causal state progression,
remediation authority and budget, idempotent return/retry rules, failure/stop
conditions and outcome/report classifications. These constrain every diagnostic
run, including evidence-limited and interrupted cases; hiding them behind a
technology-specific read could change whether another attempt or a fixed claim
is permitted. Domain playbooks, live-product rules and detailed reporting
policies already have direct conditional references. Moving the output template
alone would not bring the core below 500, while replacing the budget and
failure rules with a summary would weaken exact decisions. This alignment
retains those core blocks and adds only the shared invocation contract.
Static contract and hook regression tests protect the retained boundaries;
model-quality non-regression remains unavailable.

Comparable model-token cost and timing are UNAVAILABLE; no compatible tokenizer
was installed solely for measurement. Root `docs/` and the existing `nebius/tests/`
layout are informational structural warnings, not missing skills/resources.

## Per-skill inventory

Every row has passing static host checks and unchanged native metadata. Lines
are logical SKILL.md lines, not newline counts. Public actions/outputs and
supporting dependencies remain owned by the linked skill contract; shared
private CLI changes are listed above. The baseline was the working tree,
including accepted uncommitted work.

| Skill contract | Invocation | Lines before / after | Evals before / after |
| --- | --- | ---: | ---: |
| [agent-nebius-auth-diagnose](../agent-nebius-auth-diagnose/SKILL.md) | Implicit allowed | 198 / 207 | 11 / 14 |
| [agent-nebius-auth-setup](../agent-nebius-auth-setup/SKILL.md) | Explicit only | 230 / 239 | 9 / 12 |
| [ai-agent-design](../ai-agent-design/SKILL.md) | Implicit allowed | 330 / 339 | 18 / 21 |
| [ai-stack](../ai-stack/SKILL.md) | Implicit allowed | 409 / 418 | 23 / 26 |
| [align](../align/SKILL.md) | Implicit allowed | 353 / 362 | 6 / 9 |
| [align-skill](../align-skill/SKILL.md) | Implicit allowed | 489 / 489 | 13 / 16 |
| [app-stack](../app-stack/SKILL.md) | Implicit allowed | 255 / 264 | 20 / 23 |
| [apply-security](../apply-security/SKILL.md) | Implicit allowed | 226 / 235 | 19 / 22 |
| [attach-ubuntu](../attach-ubuntu/SKILL.md) | Explicit only | 102 / 111 | 6 / 9 |
| [brainstorm](../brainstorm/SKILL.md) | Implicit allowed | 195 / 204 | 18 / 21 |
| [code-info](../code-info/SKILL.md) | Explicit only | 153 / 162 | 6 / 9 |
| [code-review](../code-review/SKILL.md) | Implicit allowed | 278 / 287 | 20 / 23 |
| [commit](../commit/SKILL.md) | Explicit only | 335 / 348 | 6 / 9 |
| [commit-push](../commit-push/SKILL.md) | Explicit only | 226 / 235 | 6 / 9 |
| [config-claude](../config-claude/SKILL.md) | Explicit only | 137 / 149 | 7 / 10 |
| [config-codex](../config-codex/SKILL.md) | Explicit only | 410 / 422 | 6 / 9 |
| [container](../container/SKILL.md) | Implicit allowed | 219 / 228 | 18 / 21 |
| [create-learning-course](../create-learning-course/SKILL.md) | Explicit only | 208 / 217 | 17 / 20 |
| [create-pr](../create-pr/SKILL.md) | Explicit only | 448 / 457 | 8 / 11 |
| [design](../design/SKILL.md) | Implicit allowed | 362 / 372 | 25 / 28 |
| [frontend-project](../frontend-project/SKILL.md) | Implicit allowed | 178 / 187 | 11 / 14 |
| [github-workflows](../github-workflows/SKILL.md) | Implicit allowed | 116 / 125 | 6 / 9 |
| [gitignore](../gitignore/SKILL.md) | Implicit allowed | 82 / 91 | 6 / 9 |
| [global-context-management](../global-context-management/SKILL.md) | Implicit allowed | 302 / 314 | 6 / 9 |
| [helmchart](../helmchart/SKILL.md) | Implicit allowed | 217 / 226 | 7 / 10 |
| [install-grafana-mcp-for-nebius](../install-grafana-mcp-for-nebius/SKILL.md) | Explicit only | 254 / 266 | 11 / 14 |
| [linter](../linter/SKILL.md) | Implicit allowed | 71 / 80 | 6 / 9 |
| [maintain-project-specs](../maintain-project-specs/SKILL.md) | Implicit allowed | 307 / 319 | 20 / 23 |
| [merge-pr](../merge-pr/SKILL.md) | Explicit only | 107 / 116 | 6 / 9 |
| [nebius](../nebius/SKILL.md) | Implicit allowed | 127 / 136 | 22 / 25 |
| [nebius-audit-log](../nebius-audit-log/SKILL.md) | Explicit only | 119 / 128 | 13 / 16 |
| [nebius-grafana-query](../nebius-grafana-query/SKILL.md) | Implicit allowed | 258 / 267 | 14 / 17 |
| [nosleep4mac](../nosleep4mac/SKILL.md) | Explicit only | 161 / 170 | 10 / 13 |
| [optimize-pytest](../optimize-pytest/SKILL.md) | Implicit allowed | 193 / 202 | 19 / 22 |
| [project-agent-instructions](../project-agent-instructions/SKILL.md) | Explicit only | 310 / 325 | 13 / 16 |
| [prompt-session-intake](../prompt-session-intake/SKILL.md) | Explicit only | 205 / 217 | 38 / 41 |
| [publish-helm](../publish-helm/SKILL.md) | Explicit only | 181 / 190 | 6 / 9 |
| [publish-image](../publish-image/SKILL.md) | Explicit only | 165 / 174 | 6 / 9 |
| [publish-release](../publish-release/SKILL.md) | Explicit only | 226 / 235 | 6 / 9 |
| [python-project](../python-project/SKILL.md) | Implicit allowed | 257 / 266 | 12 / 15 |
| [research](../research/SKILL.md) | Implicit allowed | 238 / 247 | 18 / 21 |
| [review-pr](../review-pr/SKILL.md) | Explicit only | 277 / 286 | 6 / 9 |
| [scaffold-project](../scaffold-project/SKILL.md) | Explicit only | 324 / 333 | 8 / 11 |
| [sdlc-align-specs](../sdlc-align-specs/SKILL.md) | Internal phase | 172 / 184 | 6 / 9 |
| [sdlc-auto-steering](../sdlc-auto-steering/SKILL.md) | Internal phase | 228 / 240 | 6 / 9 |
| [sdlc-classify-failure](../sdlc-classify-failure/SKILL.md) | Internal phase | 195 / 207 | 6 / 9 |
| [sdlc-commit](../sdlc-commit/SKILL.md) | Internal phase | 193 / 205 | 6 / 9 |
| [sdlc-create-design](../sdlc-create-design/SKILL.md) | Internal phase | 221 / 233 | 6 / 9 |
| [sdlc-create-plan](../sdlc-create-plan/SKILL.md) | Internal phase | 187 / 199 | 6 / 9 |
| [sdlc-create-requirements](../sdlc-create-requirements/SKILL.md) | Internal phase | 194 / 206 | 6 / 9 |
| [sdlc-evaluate](../sdlc-evaluate/SKILL.md) | Internal phase | 273 / 285 | 7 / 10 |
| [sdlc-gather-context](../sdlc-gather-context/SKILL.md) | Internal phase | 144 / 156 | 6 / 9 |
| [sdlc-gui-test](../sdlc-gui-test/SKILL.md) | Internal phase | 156 / 168 | 6 / 9 |
| [sdlc-implement-plan](../sdlc-implement-plan/SKILL.md) | Internal phase | 252 / 265 | 6 / 9 |
| [sdlc-merge-pr](../sdlc-merge-pr/SKILL.md) | Internal phase | 178 / 190 | 6 / 9 |
| [sdlc-prepare-execution](../sdlc-prepare-execution/SKILL.md) | Internal phase | 225 / 237 | 6 / 9 |
| [sdlc-start](../sdlc-start/SKILL.md) | Explicit only | 464 / 476 | 6 / 9 |
| [sdlc-tdd](../sdlc-tdd/SKILL.md) | Internal phase | 157 / 169 | 6 / 9 |
| [sdlc-tui-test](../sdlc-tui-test/SKILL.md) | Internal phase | 140 / 152 | 6 / 9 |
| [sdlc-uat-tests](../sdlc-uat-tests/SKILL.md) | Internal phase | 166 / 178 | 6 / 9 |
| [sdlc-unit-tests](../sdlc-unit-tests/SKILL.md) | Internal phase | 152 / 164 | 6 / 9 |
| [sdlc-update-documents](../sdlc-update-documents/SKILL.md) | Internal phase | 199 / 211 | 6 / 9 |
| [sdlc-validate-codes](../sdlc-validate-codes/SKILL.md) | Internal phase | 182 / 194 | 6 / 9 |
| [sdlc-workflow-test](../sdlc-workflow-test/SKILL.md) | Explicit only | 499 / 425 | 13 / 16 |
| [shell-scripting](../shell-scripting/SKILL.md) | Implicit allowed | 92 / 101 | 6 / 9 |
| [system-design-rules](../system-design-rules/SKILL.md) | Implicit allowed | 168 / 177 | 18 / 21 |
| [task-implementer](../task-implementer/SKILL.md) | Explicit only | 349 / 367 | 30 / 33 |
| [task-implementer-test](../task-implementer-test/SKILL.md) | Explicit only | 298 / 312 | 17 / 20 |
| [terraform](../terraform/SKILL.md) | Implicit allowed | 94 / 103 | 6 / 9 |
| [troubleshoot](../troubleshoot/SKILL.md) | Implicit allowed | 614 / 623 | 30 / 33 |
| [worktree](../worktree/SKILL.md) | Explicit only | 356 / 368 | 18 / 21 |

## Sources and reproducibility

Host-sensitive changes were checked against official documentation:
[Agent Skills specification](https://agentskills.io/specification),
[Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude skills](https://code.claude.com/docs/en/skills),
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Claude memory/imports](https://code.claude.com/docs/en/memory),
[Claude CLI](https://code.claude.com/docs/en/cli-reference), and
[Claude structured output](https://platform.claude.com/docs/en/agent-sdk/structured-outputs).
Unchanged technology-specific procedures retain their own vendor-verification
requirements when actually used.

Representative read-only source checks, from this catalog root:

```bash
python3 -B align-skill/scripts/validate-skill-structure.py --policy repository --agent codex --require-evals .
python3 -B align-skill/scripts/validate-skill-structure.py --policy repository --agent claude --require-evals .
python3 -B align-skill/scripts/validate-skill-structure.py --policy agentskills --agent core --require-evals .
python3 -B global-context-management/scripts/check-plugin-manifests.py
python3 -B maintain-project-specs/scripts/validate_project_specs.py --project-root .
```

Installation and native evaluation checks used disposable private homes, with
separate evidence states. Preserve this separation when repeating them; a file
copy or declared source list cannot prove that a fresh native session loaded
and followed a skill. The remaining verification step is an authenticated
native session and proportionate model-quality evaluation on each host.
