---
name: test-software-in-container
description: "Test the current project source live in disposable containers, or explicitly test installed images, installations, upgrades, patches and migrations. Not VM, hardware or production qualification."
---

# Test Software In Container

## Help

For `$test-software-in-container --help` or `$test-software-in-container -h` (including native Claude forms), return concise help and stop before
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

## Invocation Policy

Ordinary implicit and explicit invocation are allowed. Selection supplies
guidance, not additional authority. Requests to run these tests cover inspected
test glue and disposable local containers. Planning stays read-only. Reuse
existing authorization without requiring repeated execution phrases.

Use $test-software-in-container in Codex, /test-software-in-container in Claude
Code, or /skills:test-software-in-container in the Claude plugin. Unsupported
capabilities remain visible limitations.

## Purpose

Test the current project source and exercise actual software in disposable containers, including
historical upgrades, patches and state migrations, with reusable verified
baselines and independently inspected evidence.

## When To Use

Use for current-project Linux/source suites, container-compatible installation, installed-image smoke or functional
testing, selected integration suites, upgrades, hotfixes, configuration patches
and data/state migrations. Live means executing the software and its assertions.

## When Not To Use

Use container engineering for ordinary image construction/hardening, product
live tests for VM boot or target hardware/kernel behavior, and suite-performance
tooling for unrelated unit-test optimization. Do not treat container results as
production qualification or release approval.

## Inputs

Accept a project, target software/images, authentic release metadata, state
fixtures and optional test profile. Identify the software under test; test mode
does not mean indiscriminately testing every OS package in an image.

## Public Usage

~~~text
$test-software-in-container [project-path] [mode flags] [options]
~~~

| Mode | Behavior |
| --- | --- |
| `--install` | Explicit installation: build/resolve the current project artifact, install, independently verify and run startup/smoke checks. |
| `--test` | Default mode: run source suites in the current project. Select installed target explicitly to test preinstalled software without reinstalling it. |
| `--upgrade` | Exercise supported paths from authentic predecessors to the target artifact. |
| `--patch` | Apply a declared hotfix, package/security update or configuration patch; check deltas and preserved invariants. |
| `--migration` | Execute declared data/configuration/state migrations and verify target-state invariants. |
| `--all-tests` | Run applicable lifecycle operations and test their results; add test-only cases only for profile-declared preinstalled targets. |

Bare invocation means `--test --test-target source --suite auto`. Source campaigns
are separate from lifecycle campaigns. Combining test with install/upgrade/patch/
migration requires explicit `--test-target installed`. Individual lifecycle modes
combine as independent trials, never an implicit shared-state pipeline. All-tests
is exclusive with individual modes, uses installed targets for declared image
tests and does not inherit the source default. Applicable lifecycle operations
retain smoke checks and configured compatible default suites.

| Argument or option | Meaning and default |
| --- | --- |
| `project-path` | Exact current directory by default; no automatic monorepo ascent for project/suite selection. |
| `--test-target source\|installed` | Test target; default source. Installed needs preinstalled software and an explicit CLI/profile image. |
| `--image <reference>` | Image reference; use applicable project test image/recipe, otherwise ubuntu:24.04 for prepared environments. Installed testing needs an explicit image. |
| `--platform <os/arch>` | Declared project/profile/artifact target first. Undeclared source-only tests use actual engine-native platform; unresolved lifecycle targets require clarification. |
| `--artifact <path-or-ref>` | Install/upgrade input: artifact or clearly identified source/build reference. Default profile artifact, otherwise build the frozen identified project once per platform; ask if neither is known. |
| `--profile <file>` | Default selected-project/container-tests.json; if absent derive a private profile from inspected project commands, metadata and CI. Invalid profiles fail. |
| `--engine auto\|docker\|podman` | Default auto: project-selected engine, otherwise compatible available Docker, then Podman. Freeze the choice; explicit unavailable engines block. |
| `--parallel <N>` | Positive maximum concurrent cases; default two, reduced for execution-host resource limits. Does not override suite-internal concurrency. |
| `--cache reuse\|refresh\|off` | Default reuse verified matching baselines; refresh rebuilds selected owned baselines; off disables persistent baseline reads/writes but permits campaign-local reuse. |
| `--output <directory>` | Newly created private report directory; default task-owned storage outside Git. Reject collisions; never overwrite evidence. |
| `--past-releases <N>` | Upgrade-only positive maximum preceding stable releases, including patches; default four. Zero is invalid. |
| `--from-releases <list>` | Upgrade-only comma-separated exact predecessors; mutually exclusive with an explicit release count. |
| `--upgrade-path direct\|sequential\|both` | Upgrade-only; default direct. Sequential paths require explicit support; both deduplicates identical terminal paths. |
| `--suite <name>` | Repeatable project-defined suite; default auto uses compatible declared defaults or one documented source default. Explicit names replace auto. Never shell text. |
| `--list-suites` | Static names, purpose, applicability, origin and defaults; only project-path/profile/test-target modifiers, no imports or collection. |
| `--preflight-only` | Run bounded tooling/environment probes without collection, tests or product lifecycle actions; report and clean owned probe resources. |
| `--plan-only` | Describe cases/unresolved inputs without collection, tests, pulls, builds or containers. |
| `-h, --help` | Side-effect-free help as defined above. |

Resolve relative CLI and profile filesystem paths from the selected project,
including an external profile's paths. Never reinterpret image/remote references
as paths. Read ancestor instructions even though project selection does not ascend.

Plan-only and preflight-only are exclusive. Reject explicitly irrelevant options,
including artifact for a test/patch/migration-only selection and upgrade selectors
without upgrade. Patch payloads and migration tools/source/target definitions
belong in the profile. No old skill-name, base-os, candidate, matrix or runtime
flag aliases are provided.

CLI values override profile defaults. Explicit per-environment image/platform
declarations are binding; contradictions fail. Heterogeneous images belong in
the profile. Resolve changed images/platforms before freezing their digests; the
offline planner rejects reinterpretation of frozen identities. Inspect image OS
and platform, record native/emulated execution, and do not infer target-VM identity.

These are skill inputs, not a bundled universal execution CLI. The agent/harness
owns image/artifact resolution, engine selection, resource limits and output
directories; the offline helpers own only documented planning/reporting inputs.

For complete flag explanations, suite categories and examples, read [README](README.md).
For `--list-suites`, inspect only static selected-project declarations/docs and
stop after reporting; no pulls, builds, imports, collection or execution.

## Required Reads

- Read project instructions, supported lifecycle contracts, installers, test
  suites, state schemas, service definitions and existing harnesses.
- Read [workflow](references/workflow.md) for authentic baselines and execution.
- Read [runtime preflight](references/runtime-preflight.md) before runtime setup.
- Read [suite evidence](references/suite-evidence.md) for source suites, inventories or runner receipts.
- Read [profile](references/profile.md) when preparing helper inputs or evidence.
- Read relevant [container checks](references/container-checks.md) when adapting
  runtime, service or package-manager observations; verify version-sensitive
  commands against current official documentation.
- Treat assets/*.json as synthetic structural examples, not executable proof.

## Process

1. Identify software, selected operations, target environments and supported
   paths. Default to source test in the exact selected project. Resolve suite auto:
   compatible declared defaults, otherwise one unambiguous documented default
   entrypoint; absent/empty/ambiguous source selection needs a choice. Discover authoritative predecessor order only
   for upgrade; retain missing selected artifacts as blockers.
2. Define operation-specific inputs, required assertions, suites, services,
   supported repeats/recovery, exclusions and resource/time budgets. Missing
   applicability is unresolved, not permission to exclude a mode. Show case count.
   Plan-only ends with this description and unresolved identities.
3. Preflight controller tools, container/runtime capabilities, complete bundle
   accessibility and execution-host resource visibility before expensive setup.
   Preflight-only ends here without product install/upgrade/patch/migration.
4. Freeze complete execution inputs, including relevant dirty bytes, file and directory modes,
   symlink targets and required helpers. Build target artifacts once per platform.
   Prepare minimal common bases and quiescent, independently verified historical
   or state baselines. Keep candidate/controller dependencies out of predecessors.
5. For suites, separately collect and freeze the expected inventory against the
   frozen inputs before execution. Collection can execute imports: run it only
   during an authorized full run in disposable preparation, never plan/preflight.
   Expand the profile with scripts/plan_matrix.py. Start an actual planned case
   per distinct setup before bounded fanout; retain its result and all coverage.
   Every independent trial restores fresh writable volumes and external state.
6. Exercise product-owned entrypoints. Source tests require source-origin and
   actual restricted-user traversal checks; installed probes disable source imports.
   Prepare test dependencies separately so they cannot replace/shadow installed targets. Check required actual startup, smoke behavior,
   selected project suites, patch deltas and migration invariants.
   Setup of a predecessor is not candidate install coverage.
7. Classify failures by owner and preserve attempts/interventions. Use
   scripts/check_evidence.py to check plan-bound observations and render scoped
   summaries. Independently inspect evidence; a valid report is not runtime proof.
8. Collect bounded diagnostics before exactly owned cleanup. Report phase timing,
   cache reuse, native/emulated execution, required missing evidence and any
   retained resources with cleanup owner and deadline.

## Writes

Create private profiles, frozen bundles, reports and task-owned disposable
resources; create scoped test glue when authorized. Reports use private directories
(mode 0700) and new-only files (0600). Helpers are offline Python 3.11+ standard
library and never execute profile commands. The optional pytest reporter requires
pytest in the project test environment; it observes hooks and never runs a container.
Use source_manifest.py on an explicit reviewed input list; verify the staged copy
and real restricted-user access separately. No new daemon, global runtime,
provider integration or universal execution framework is required.

## Idempotency

Freeze target artifacts and full execution-bundle identity for comparable trials.
Keep baseline keys limited to baseline-affecting OS/platform, historical artifacts,
dependency closure, setup recipes and fixtures. Candidate-only changes must not
force unrelated predecessor rebuilds. Verify actual cached contents before reuse.
Refresh preserves old evidence; cache off does not prune daemon-wide caches.

## Failure Handling

Distinguish product assertions, unavailable artifacts/tools, unsupported runtime,
harness defects and interrupted trials. Stop affected setup fanout, not independent
valid cases. Do not repeatedly retry product failures. Repair the proven owner,
retain failure evidence and replay fresh after an intervention. Missing required
checks remain incomplete; no retroactive exclusion or manufactured PASS.

## Must Not

- Do not equate install exit status, version strings or process liveness with
  successful startup, functional behavior, patch correctness or data preservation.
- Do not preinstall target dependencies in predecessors, downgrade images to
  fabricate history, patch old product code to manufacture fixtures, or substitute
  a look-alike installer for the supported product workflow.
- Do not silently install software in installed-target test mode, use a source checkout as an
  installed-artifact probe, or count manual repairs as successful product recovery.
- Do not default to privileged containers, host namespaces, daemon sockets or
  writable host mounts. Unsupported required capabilities remain blockers.
- Do not alter production, release gates, host engines/security or cloud resources
  implicitly. Keep secrets and customer data out of images, bundles and reports.
- Do not prune unrelated resources. Obey host headroom policy on the actual
  execution host as well as the controller; bound CPU, memory, disk and deadlines.

## Completion Criteria

Every selected required assertion has an observed outcome. Suite PASS needs a
separately frozen inventory, every expected test accounted for, terminal runner
evidence and at least one ordinary passing test per suite/case. Exact predeclared
exceptions cannot excuse setup/teardown errors or unrelated assertion failures.
Unexpected passes fail; all-skipped, missing or interrupted coverage is incomplete.
Reject unsupported pytest retries/distributed execution; preserve partial evidence. Failed checks yield
FAIL; missing/blocked checks yield INCOMPLETE. All-N/A campaigns do not pass.
Reports identify operation and suite coverage, artifacts/state/image identities,
cache reuse, phase timing and cleanup. No VM boot, target kernel/hardware,
production connectivity or release approval claim follows from these results.

## Validation

Run python3 -B -m unittest discover -s scripts -p 'test_*.py' from this skill.
Use canonical trigger CSV and quality cases for routing/output evaluation.
Separate static/helper tests, disposable installation, actual container trials,
native triggering and comparative model quality.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise source/installed target and operation/suite matrix, PASS/FAIL/INCOMPLETE, report paths,
frozen identities, native/emulated mode, phase timing/cache reuse, exclusions
and cleanup. Name missing evidence and distinguish package/startup assertions
from named functional/integration results. Never promise unmeasured speed gains.

Example: installation and the named CLI smoke suite passed on Ubuntu 24.04,
linux/amd64; API integration, VM boot and production connectivity were not tested.
