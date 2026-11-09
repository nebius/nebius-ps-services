# Test software in containers

Run `$test-software-in-container` from your project to test its current source
in a disposable container. The skill reads the project's test instructions and
uses its existing harness. It supports other languages and test runners as well
as pytest; no product, cloud provider or fixed test count is assumed.

```text
$test-software-in-container
$test-software-in-container --list-suites
$test-software-in-container --suite integration
$test-software-in-container --suite unit --suite cli
$test-software-in-container ./component --plan-only
$test-software-in-container --test-target installed --image example/software:2.0
$test-software-in-container --install
$test-software-in-container --upgrade --past-releases 4
```

These are **agent skill inputs**, not a universal executable CLI. The agent
inspects the project, resolves inputs and uses its reviewed harness. The bundled
Python helpers only provide static discovery, input fingerprints, offline plans
and evidence checking. The optional pytest plugin observes runner hooks.

## Defaults and project selection

Bare invocation means `--test --test-target source --suite auto`. The selected
project is the exact current directory, or the explicit positional project path.
The skill does not ascend a monorepo to select another project's tests. It still
reads applicable ancestor instructions.

It first looks for `container-tests.json` in that project. If absent, it inspects
documented test commands, project metadata, CI and existing test-container recipes
to derive a private profile. Invalid or unreadable profiles fail rather than
trigger fallback. Source `auto` uses compatible profile defaults, otherwise one
unambiguous documented default test entrypoint. Missing, ambiguous or explicitly
empty source defaults require a suite choice; they never mean run everything.

Relative CLI filesystem paths and profile filesystem paths resolve from the
selected project, even when the profile itself is outside that project. An external
profile does not change the project. Image references, URLs and other remote
references are not filesystem paths. Reviewed command arguments keep their harness
semantics; do not blindly rewrite every argument that resembles a path.

## Modes

| Flag | Behavior |
| --- | --- |
| `--test` | Default. Run selected tests against the current source, or against preinstalled software when installed target is explicit. |
| `--install` | Resolve/build the current project artifact, install it, independently inspect the installation and run startup/smoke checks. No history is required. |
| `--upgrade` | Install authentic predecessors in verified baselines and exercise supported upgrade paths to the same frozen target artifact. |
| `--patch` | Apply a declared hotfix, package/security update or configuration patch; verify its delta and preserved invariants, even without a version change. |
| `--migration` | Exercise a declared data/configuration/state migration and verify meaningful target-state invariants. A pure data migration needs no invented package predecessor. |
| `--all-tests` | Run applicable install/upgrade/patch/migration operations, their smoke checks and compatible configured default suites. Add standalone installed-image cases only for explicitly declared preinstalled targets. |

Individual lifecycle modes can combine, each with fresh independent state.
Source testing is a separate campaign. Combining `--test` with a lifecycle mode
requires explicit `--test-target installed`. `--all-tests` is exclusive with
individual modes, excludes source suites and does not discover every project suite.
Explicit non-applicability is recorded; missing lifecycle declarations remain
blocked. It cannot manufacture a passing standalone software test on a bare OS.

## All arguments and options

| Argument / flag | Default | Meaning and constraints |
| --- | --- | --- |
| `project-path` | Current directory | Exact project containing software, test commands and release metadata. |
| `--test-target source\|installed` | `source` for test | Source uses frozen current source and test dependencies. Installed requires preinstalled software in an explicit CLI/profile image; missing software fails without reinstalling it. Only valid with selected test mode; all-tests internally selects installed targets. |
| `--suite <name>` | `auto` | Repeatable project-defined selection. Explicit names replace auto; auto cannot combine with names. Every requested suite must apply to every generated case, including normal/fault trials. No silent filtering. |
| `--list-suites` | Off | Report names, categories, purpose, applicability, origin and default selections using static files only. May combine only with project-path, profile and optional test-target filter. No imports, collection, installs, pulls, builds or containers. |
| `--profile <file>` | Project `container-tests.json` | Declares environments, operations, inputs, suites and policies. If absent, derive a private reviewed profile from the project. An explicitly named missing file is an error. |
| `--image <reference>` | Applicable project test image/recipe; otherwise `ubuntu:24.04` | One image reference selector. A test Dockerfile is harness metadata, built/resolved during a full run; do not pass a Dockerfile path as an image. Installed testing requires an explicit CLI/profile image. |
| `--platform <os/arch>` | Declared project/profile/artifact target | Example `linux/amd64`. For source-only tests with no declaration, use the observed container engine's native platform. Never infer it from the controller. Unresolved lifecycle targets require clarification; unavailable/ambiguous engines block. |
| `--artifact <path-or-ref>` | Configured artifact, otherwise frozen current project built once per platform | Install/upgrade input only. Resolve the current artifact deliberately; do not select arbitrary stale files in `dist/`. |
| `--engine auto\|docker\|podman` | `auto` | Use the project-selected engine, otherwise compatible available Docker, then Podman. Freeze the choice. Does not install or reconfigure an engine. |
| `--parallel <N>` | `2` | Positive maximum concurrent cases, reduced for actual execution-host limits. Suites run serially unless the project declares internal concurrency safe; account for both levels. |
| `--cache reuse\|refresh\|off` | `reuse` | Reuse verified matching source environments/baselines; refresh rebuilds selected owned caches; off disables persistent reads/writes while permitting campaign-local reuse. Never means global engine pruning or disabling every engine cache. |
| `--output <directory>` | New private task-owned directory outside Git | Store frozen inputs and reports in a new directory. Relative explicit paths use the selected project. Reject collisions; never overwrite previous evidence. |
| `--past-releases <N>` | `4` | Upgrade-only positive maximum stable predecessors of the selected software, including patch releases. Uses authoritative release order; fewer available releases are reported. Zero is invalid. |
| `--from-releases <list>` | Unset | Upgrade-only comma-separated exact predecessor labels. Exclusive with an explicit past-releases count; unavailable selected artifacts remain blockers. |
| `--upgrade-path direct\|sequential\|both` | `direct` | Upgrade-only. Sequential requires declared supported intermediate paths/artifacts; both deduplicates identical terminal paths. |
| `--plan-only` | Off | Static plan and unresolved inputs only. No collection, tests, pulls, builds or container execution. |
| `--preflight-only` | Off | Bounded environment/capability probes and exact cleanup. May resolve/pull probe images within task authority; no collection, suite execution or product lifecycle actions. Exclusive with plan-only. |
| `-h, --help` | Off | Help only; after loading the skill, no further tools, project inspection or mutations. |

CLI values override profile defaults; explicit per-environment image/platform
bindings remain authoritative and conflicts fail. Resolve changed selectors before
freezing digests; never reinterpret an old digest under a different OS/platform.
The skill reports the actual platform and native/emulated execution.

Retired flags `--base-os`, `--candidate`, `--matrix`, `--runtime` and the old skill
name have no aliases. Use `--image`, `--artifact`, `--profile`, `--engine` respectively.

## What can a suite be?

Suites are **project-defined names**, not built-in commands or a guarantee that
the following tests exist. A profile supplies their commands, inputs and assertions.

| Example category/name | Typical purpose |
| --- | --- |
| `smoke` | A small set of essential behavior checks. |
| `unit` | Source components in isolation. |
| `integration` | Interaction between components or dependencies in Linux. |
| `api`, `cli` | Declared API or command-line interface behavior. |
| `systemd` | Real service-manager installation, startup, restart or job behavior. |
| `e2e` | A declared complete workflow in the disposable environment. |
| `regression` | Scenarios covering previously observed failures. |
| `performance` | Explicit workloads with budgets and pass criteria; comparable environments are required for speed claims. |
| `security` | Explicit bounded checks against disposable targets with declared assertions and authority. |

Projects can use other names or several suites in one category. Naming a suite
`systemd` does not add privileges; its required capabilities must be declared and
probed. Naming a suite `migration` selects that project's suite; `--migration`
selects a lifecycle operation with source/target-state checks.

For source, `auto` follows the selection rules above. For installed/lifecycle
campaigns, `auto` uses only compatible configured defaults, otherwise the required
core smoke checks. It never pulls arbitrary source suites into installed testing.
An explicitly empty source default asks for a choice; an empty installed/lifecycle
default means core checks only. `--suite auto --suite integration` is invalid.

## Evidence and speed

Freeze source, tests, dependency closure and harness, including file/directory
modes and symlink targets. Check staged contents and restricted-user access before
testing. Collect the expected test inventory in a separate process against those
same frozen inputs before execution. Collection itself can run imports, so it
belongs to authorized disposable preparation, not static planning or preflight.

Every selected suite reports observed IDs, runner/version/selection, phases,
completion, exit status, skips/deselections and timings. At least one ordinary
passing test per suite/case is required. Assertion/setup/teardown/runner errors
fail; missing, interrupted, empty or all-skipped coverage is incomplete. Exceptions
must be exact and declared before execution; prior failures stay visible.

Cache source environments separately from authentic release baselines. Start fresh
writable state per trial, reuse frozen artifacts, and measure preparation, collection,
execution, verification and cleanup separately. No speedup is claimed without
comparable cold/warm measurements. Source passes do not prove installed artifacts;
container passes do not prove another kernel, VM boot or production behavior.

## Profiles and helpers

- [Profile and report contract](references/profile.md): v2 schemas and offline helpers.
- [Suite receipts and optional pytest adapter](references/suite-evidence.md): neutral evidence and two-process collection/execution.
- [Workflow](references/workflow.md): authentic predecessor baselines and independent verification.
- [Runtime preflight](references/runtime-preflight.md): ordinary process, service manager and kernel capability boundaries.
- [Source profile](assets/source-test.json) and [neutral result](assets/neutral-suite-result.json): synthetic structural examples; replace all identities before live use.

Python helpers need Python 3.11+. They never execute profile commands. The optional
pytest reporter needs pytest in the chosen project's test environment. Other
runners can emit the same neutral contract without importing pytest.
